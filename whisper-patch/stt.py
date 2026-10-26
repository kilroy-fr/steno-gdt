# ============================================================================
# PATCH Steno — Wiederholschleifen unterbinden
# ============================================================================
# Unveraenderte Kopie von faster_whisper_server/routers/stt.py aus dem Image
# fedirz/faster-whisper-server:latest-cuda, mit genau einer inhaltlichen
# Aenderung: die drei Decoder-Optionen unten. Sie wird per Bind-Mount ueber die
# Datei im Container gelegt (siehe docker-compose.yml, Dienst faster-whisper).
#
# Grund: der Server reicht nur task/language/initial_prompt/word_timestamps/
# temperature/vad_filter/hotwords an faster-whisper durch. Alles andere bleibt
# auf dessen Defaults — darunter condition_on_previous_text=True, das dem Modell
# seine eigene Ausgabe als Kontext zurueckgibt und damit die Wiederholschleife
# ueberhaupt erst traegt. Gemessen an einer 36-min-Aufnahme (Log vom 17.09.2026,
# 13:42): 9612 von 10376 Woertern waren Schleife, rund 30 Minuten Gespraech
# gingen verloren. Ueber die HTTP-Schnittstelle ist dagegen nichts auszurichten.
#
# Aktualisieren nach einem Image-Update: neue Vorlage aus dem Image holen und
# mit stt.py.upstream vergleichen, Rezept in whisper-patch/README.md.
# ============================================================================

from __future__ import annotations

import asyncio
from io import BytesIO
import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import (
    APIRouter,
    Form,
    Query,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import StreamingResponse
from fastapi.websockets import WebSocketState
from faster_whisper.vad import VadOptions, get_speech_timestamps
from pydantic import AfterValidator

from faster_whisper_server.api_models import (
    DEFAULT_TIMESTAMP_GRANULARITIES,
    TIMESTAMP_GRANULARITIES_COMBINATIONS,
    CreateTranscriptionResponseJson,
    CreateTranscriptionResponseVerboseJson,
    TimestampGranularities,
    TranscriptionSegment,
)
from faster_whisper_server.asr import FasterWhisperASR
from faster_whisper_server.audio import AudioStream, audio_samples_from_file
from faster_whisper_server.config import (
    SAMPLES_PER_SECOND,
    Language,
    ResponseFormat,
    Task,
)
from faster_whisper_server.dependencies import ConfigDependency, ModelManagerDependency, get_config
from faster_whisper_server.text_utils import segments_to_srt, segments_to_text, segments_to_vtt
from faster_whisper_server.transcriber import audio_transcriber

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable

    from faster_whisper.transcribe import TranscriptionInfo


logger = logging.getLogger(__name__)

router = APIRouter()


def segments_to_response(
    segments: Iterable[TranscriptionSegment],
    transcription_info: TranscriptionInfo,
    response_format: ResponseFormat,
) -> Response:
    segments = list(segments)
    match response_format:
        case ResponseFormat.TEXT:
            return Response(segments_to_text(segments), media_type="text/plain")
        case ResponseFormat.JSON:
            return Response(
                CreateTranscriptionResponseJson.from_segments(segments).model_dump_json(),
                media_type="application/json",
            )
        case ResponseFormat.VERBOSE_JSON:
            return Response(
                CreateTranscriptionResponseVerboseJson.from_segments(segments, transcription_info).model_dump_json(),
                media_type="application/json",
            )
        case ResponseFormat.VTT:
            return Response(
                "".join(segments_to_vtt(segment, i) for i, segment in enumerate(segments)), media_type="text/vtt"
            )
        case ResponseFormat.SRT:
            return Response(
                "".join(segments_to_srt(segment, i) for i, segment in enumerate(segments)), media_type="text/plain"
            )


def format_as_sse(data: str) -> str:
    return f"data: {data}\n\n"


def segments_to_streaming_response(
    segments: Iterable[TranscriptionSegment],
    transcription_info: TranscriptionInfo,
    response_format: ResponseFormat,
) -> StreamingResponse:
    def segment_responses() -> Generator[str, None, None]:
        for i, segment in enumerate(segments):
            if response_format == ResponseFormat.TEXT:
                data = segment.text
            elif response_format == ResponseFormat.JSON:
                data = CreateTranscriptionResponseJson.from_segments([segment]).model_dump_json()
            elif response_format == ResponseFormat.VERBOSE_JSON:
                data = CreateTranscriptionResponseVerboseJson.from_segment(
                    segment, transcription_info
                ).model_dump_json()
            elif response_format == ResponseFormat.VTT:
                data = segments_to_vtt(segment, i)
            elif response_format == ResponseFormat.SRT:
                data = segments_to_srt(segment, i)
            yield format_as_sse(data)

    return StreamingResponse(segment_responses(), media_type="text/event-stream")


def handle_default_openai_model(model_name: str) -> str:
    """Exists because some callers may not be able override the default("whisper-1") model name.

    For example, https://github.com/open-webui/open-webui/issues/2248#issuecomment-2162997623.
    """
    config = get_config()  # HACK
    if model_name == "whisper-1":
        logger.info(f"{model_name} is not a valid model name. Using {config.whisper.model} instead.")
        return config.whisper.model
    return model_name


# ---- PATCH Steno: Decoder-Optionen gegen Wiederholschleifen -------------------
# Die Reihenfolge ist absichtlich diese: (1) nimmt der Schleife den Treibstoff,
# (2) macht die Notbremse ueberhaupt erst scharf, (3) raeumt den Rest weg.
_TEMPERATURE_FALLBACK = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
_HALLUCINATION_SILENCE_THRESHOLD = 2.0


def _anti_loop_options(temperature: float, word_timestamps: bool) -> dict:
    """Decoder-Optionen, die der Server sonst auf ihren Defaults liegen laesst."""
    options: dict = {
        # (1) Der eigentliche Fix. Default ist True: jedes Fenster bekommt den
        # Text des vorigen als Prompt. Faengt das Modell einmal an, einen
        # Baustein zu wiederholen, bestaerkt es sich damit selbst und kommt bis
        # zum Dateiende nicht mehr heraus. Der Preis ist etwas weniger Kontext
        # ueber Fenstergrenzen (Eigennamen, angefangene Saetze) — verglichen mit
        # dem Totalverlust einer halben Stunde Gespraech ist das kein Handel.
        "condition_on_previous_text": False,

        # (2) faster-whisper prueft jedes Segment mit
        # compression_ratio_threshold=2.4 auf Entartung: wiederholter Text laesst
        # sich auffaellig gut komprimieren. Die Pruefung kann aber nur greifen,
        # wenn es eine hoehere Temperatur gibt, auf die zurueckgefallen werden
        # kann — sonst wird das Segment zwar als "failed" erkannt und dann doch
        # uebernommen. Genau das passiert hier: faster-whispers eigener Default
        # ist die Leiter unten, der Server flacht sie auf den Skalar 0.0 ab und
        # schaltet die Notbremse damit unbemerkt aus. Eine ausdruecklich
        # gesetzte Temperatur bleibt unangetastet.
        "temperature": _TEMPERATURE_FALLBACK if temperature == 0.0 else temperature,
    }
    if word_timestamps:
        # (3) Erkennt Halluzinationen, die in Stille hineinerzeugt wurden, und
        # ueberspringt die Stille. Braucht Wort-Zeitstempel; /analyze fordert die
        # ohnehin fuer die Sprecherzuordnung an.
        options["hallucination_silence_threshold"] = _HALLUCINATION_SILENCE_THRESHOLD
    return options
# ---- Ende Patch -------------------------------------------------------------


ModelName = Annotated[str, AfterValidator(handle_default_openai_model)]


@router.post(
    "/v1/audio/translations",
    response_model=str | CreateTranscriptionResponseJson | CreateTranscriptionResponseVerboseJson,
)
def translate_file(
    config: ConfigDependency,
    model_manager: ModelManagerDependency,
    file: Annotated[UploadFile, Form()],
    model: Annotated[ModelName | None, Form()] = None,
    prompt: Annotated[str | None, Form()] = None,
    response_format: Annotated[ResponseFormat | None, Form()] = None,
    temperature: Annotated[float, Form()] = 0.0,
    stream: Annotated[bool, Form()] = False,
    vad_filter: Annotated[bool, Form()] = False,
) -> Response | StreamingResponse:
    if model is None:
        model = config.whisper.model
    if response_format is None:
        response_format = config.default_response_format
    with model_manager.load_model(model) as whisper:
        segments, transcription_info = whisper.transcribe(
            file.file,
            task=Task.TRANSLATE,
            initial_prompt=prompt,
            vad_filter=vad_filter,
            **_anti_loop_options(temperature, word_timestamps=False),
        )
        segments = TranscriptionSegment.from_faster_whisper_segments(segments)

        if stream:
            return segments_to_streaming_response(segments, transcription_info, response_format)
        else:
            return segments_to_response(segments, transcription_info, response_format)


# HACK: Since Form() doesn't support `alias`, we need to use a workaround.
async def get_timestamp_granularities(request: Request) -> TimestampGranularities:
    form = await request.form()
    if form.get("timestamp_granularities[]") is None:
        return DEFAULT_TIMESTAMP_GRANULARITIES
    timestamp_granularities = form.getlist("timestamp_granularities[]")
    assert (
        timestamp_granularities in TIMESTAMP_GRANULARITIES_COMBINATIONS
    ), f"{timestamp_granularities} is not a valid value for `timestamp_granularities[]`."
    return timestamp_granularities


# https://platform.openai.com/docs/api-reference/audio/createTranscription
# https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L8915
@router.post(
    "/v1/audio/transcriptions",
    response_model=str | CreateTranscriptionResponseJson | CreateTranscriptionResponseVerboseJson,
)
def transcribe_file(
    config: ConfigDependency,
    model_manager: ModelManagerDependency,
    request: Request,
    file: Annotated[UploadFile, Form()],
    model: Annotated[ModelName | None, Form()] = None,
    language: Annotated[Language | None, Form()] = None,
    prompt: Annotated[str | None, Form()] = None,
    response_format: Annotated[ResponseFormat | None, Form()] = None,
    temperature: Annotated[float, Form()] = 0.0,
    timestamp_granularities: Annotated[
        TimestampGranularities,
        # WARN: `alias` doesn't actually work.
        Form(alias="timestamp_granularities[]"),
    ] = ["segment"],
    stream: Annotated[bool, Form()] = False,
    hotwords: Annotated[str | None, Form()] = None,
    vad_filter: Annotated[bool, Form()] = False,
) -> Response | StreamingResponse:
    if model is None:
        model = config.whisper.model
    if language is None:
        language = config.default_language
    if response_format is None:
        response_format = config.default_response_format
    timestamp_granularities = asyncio.run(get_timestamp_granularities(request))
    if timestamp_granularities != DEFAULT_TIMESTAMP_GRANULARITIES and response_format != ResponseFormat.VERBOSE_JSON:
        logger.warning(
            "It only makes sense to provide `timestamp_granularities[]` when `response_format` is set to `verbose_json`. See https://platform.openai.com/docs/api-reference/audio/createTranscription#audio-createtranscription-timestamp_granularities."  # noqa: E501
        )
    with model_manager.load_model(model) as whisper:
        segments, transcription_info = whisper.transcribe(
            file.file,
            task=Task.TRANSCRIBE,
            language=language,
            initial_prompt=prompt,
            word_timestamps="word" in timestamp_granularities,
            vad_filter=vad_filter,
            hotwords=hotwords,
            **_anti_loop_options(temperature, "word" in timestamp_granularities),
        )
        segments = TranscriptionSegment.from_faster_whisper_segments(segments)

        if stream:
            return segments_to_streaming_response(segments, transcription_info, response_format)
        else:
            return segments_to_response(segments, transcription_info, response_format)


async def audio_receiver(ws: WebSocket, audio_stream: AudioStream) -> None:
    config = get_config()  # HACK
    try:
        while True:
            bytes_ = await asyncio.wait_for(ws.receive_bytes(), timeout=config.max_no_data_seconds)
            logger.debug(f"Received {len(bytes_)} bytes of audio data")
            audio_samples = audio_samples_from_file(BytesIO(bytes_))
            audio_stream.extend(audio_samples)
            if audio_stream.duration - config.inactivity_window_seconds >= 0:
                audio = audio_stream.after(audio_stream.duration - config.inactivity_window_seconds)
                vad_opts = VadOptions(min_silence_duration_ms=500, speech_pad_ms=0)
                # NOTE: This is a synchronous operation that runs every time new data is received.
                # This shouldn't be an issue unless data is being received in tiny chunks or the user's machine is a potato.  # noqa: E501
                timestamps = get_speech_timestamps(audio.data, vad_opts)
                if len(timestamps) == 0:
                    logger.info(f"No speech detected in the last {config.inactivity_window_seconds} seconds.")
                    break
                elif (
                    # last speech end time
                    config.inactivity_window_seconds - timestamps[-1]["end"] / SAMPLES_PER_SECOND
                    >= config.max_inactivity_seconds
                ):
                    logger.info(f"Not enough speech in the last {config.inactivity_window_seconds} seconds.")
                    break
    except TimeoutError:
        logger.info(f"No data received in {config.max_no_data_seconds} seconds. Closing the connection.")
    except WebSocketDisconnect as e:
        logger.info(f"Client disconnected: {e}")
    audio_stream.close()


@router.websocket("/v1/audio/transcriptions")
async def transcribe_stream(
    config: ConfigDependency,
    model_manager: ModelManagerDependency,
    ws: WebSocket,
    model: Annotated[ModelName | None, Query()] = None,
    language: Annotated[Language | None, Query()] = None,
    response_format: Annotated[ResponseFormat | None, Query()] = None,
    temperature: Annotated[float, Query()] = 0.0,
    vad_filter: Annotated[bool, Query()] = False,
) -> None:
    if model is None:
        model = config.whisper.model
    if language is None:
        language = config.default_language
    if response_format is None:
        response_format = config.default_response_format
    await ws.accept()
    transcribe_opts = {
        "language": language,
        "temperature": temperature,
        "vad_filter": vad_filter,
        "condition_on_previous_text": False,
    }
    with model_manager.load_model(model) as whisper:
        asr = FasterWhisperASR(whisper, **transcribe_opts)
        audio_stream = AudioStream()
        async with asyncio.TaskGroup() as tg:
            tg.create_task(audio_receiver(ws, audio_stream))
            async for transcription in audio_transcriber(asr, audio_stream, min_duration=config.min_duration):
                logger.debug(f"Sending transcription: {transcription.text}")
                if ws.client_state == WebSocketState.DISCONNECTED:
                    break

                if response_format == ResponseFormat.TEXT:
                    await ws.send_text(transcription.text)
                elif response_format == ResponseFormat.JSON:
                    await ws.send_json(CreateTranscriptionResponseJson.from_transcription(transcription).model_dump())
                elif response_format == ResponseFormat.VERBOSE_JSON:
                    await ws.send_json(
                        CreateTranscriptionResponseVerboseJson.from_transcription(transcription).model_dump()
                    )

    if ws.client_state != WebSocketState.DISCONNECTED:
        logger.info("Closing the connection.")
        await ws.close()
