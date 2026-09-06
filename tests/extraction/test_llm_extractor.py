"""
Mocked-API tests for LLMExtractor — no network.

Covers the P0-1(b) hardening: a response that will not parse must be retried
once before the extractor gives up, and `reasoning_effort` must be sent only
for models that accept it.
"""
import pytest

from src.extraction.llm_extractor import LLMExtractor


# --- fake OpenAI client -----------------------------------------------------

class _Msg:
    def __init__(self, content): self.content = content


class _Choice:
    def __init__(self, content): self.message = _Msg(content)


class _Usage:
    prompt_tokens = 10
    completion_tokens = 5
    total_tokens = 15


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = _Usage()


class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kw):
        self.calls.append(kw)
        content = self._responses.pop(0) if self._responses else "{}"
        return _Resp(content)


class _FakeClient:
    def __init__(self, responses):
        self.chat = type("chat", (), {"completions": _FakeCompletions(responses)})()

    @property
    def calls(self):
        return self.chat.completions.calls


@pytest.fixture
def make_extractor(monkeypatch, tmp_path):
    """Build an extractor whose OpenAI client and PDF rendering are faked."""
    def _make(responses, model="gpt-5-nano", reasoning_effort="minimal"):
        # keep Langfuse out of the way
        import src.langfuse_client as lc
        monkeypatch.setattr(lc, "get_langfuse", lambda: None)

        ex = LLMExtractor(provider="openai", strategy="direct")
        ex.config = dict(ex.config, model=model, api_key="sk-test",
                         reasoning_effort=reasoning_effort)
        monkeypatch.setattr(ex, "_pdf_to_image", lambda p: b"fake-png")

        client = _FakeClient(responses)
        import openai
        monkeypatch.setattr(openai, "OpenAI", lambda **kw: client)

        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        return ex, client, pdf
    return _make


VALID = '{"invoice_number": "INV-1", "items": []}'


# --- retry behaviour --------------------------------------------------------

def test_no_retry_when_the_first_response_parses(make_extractor):
    ex, client, pdf = make_extractor([VALID])
    out = ex.extract(pdf, "invoice")
    assert out["invoice_number"] == "INV-1"
    assert len(client.calls) == 1


def test_retries_once_when_the_response_is_unparseable(make_extractor):
    ex, client, pdf = make_extractor(["I cannot do that", VALID])
    out = ex.extract(pdf, "invoice")
    assert out["invoice_number"] == "INV-1"
    assert len(client.calls) == 2, "should have retried exactly once"


def test_gives_up_after_one_retry_and_returns_empty(make_extractor):
    ex, client, pdf = make_extractor(["garbage", "still garbage"])
    out = ex.extract(pdf, "invoice")
    assert out == {}, "an unparseable result must be empty, never partial"
    assert len(client.calls) == 2, "exactly one retry, not an unbounded loop"


def test_empty_result_is_what_the_pipeline_turns_into_review(make_extractor):
    # the safety net depends on this contract
    ex, _client, pdf = make_extractor(["garbage", "garbage"])
    assert ex.extract(pdf, "invoice") == {}


# --- reasoning_effort -------------------------------------------------------

def test_reasoning_effort_is_sent_when_configured(make_extractor):
    ex, client, pdf = make_extractor([VALID], model="gpt-5-nano",
                                     reasoning_effort="minimal")
    ex.extract(pdf, "invoice")
    assert client.calls[0]["reasoning_effort"] == "minimal"


def test_reasoning_effort_is_omitted_when_not_configured(make_extractor):
    ex, client, pdf = make_extractor([VALID], model="gpt-4.1-nano",
                                     reasoning_effort=None)
    ex.extract(pdf, "invoice")
    assert "reasoning_effort" not in client.calls[0]


class TestPdfToImage:
    """
    A raster input (a photo, a screenshot) is already pixels. Rendering it as if
    it were a 72-DPI vector page upscales it ~4x, which adds no information and
    multiplies the API payload. Vector PDFs must still rasterise at 300 DPI.
    """

    def _extractor(self):
        return LLMExtractor(provider="openai", strategy="direct")

    def test_raster_input_is_not_upscaled(self, tmp_path):
        from PIL import Image
        import io as _io
        src = tmp_path / "photo.png"
        Image.new("RGB", (800, 600), "white").save(src)

        out = self._extractor()._pdf_to_image(src)
        got = Image.open(_io.BytesIO(out))
        assert got.size == (800, 600), (
            f"raster input was resampled to {got.size}; it must pass through "
            "at its native resolution"
        )

    def test_oversized_raster_is_downscaled_not_upscaled(self, tmp_path):
        from PIL import Image
        import io as _io
        src = tmp_path / "huge.png"
        Image.new("RGB", (6000, 4000), "white").save(src)

        got = Image.open(_io.BytesIO(self._extractor()._pdf_to_image(src)))
        assert max(got.size) <= 4000, "a very large raster should be capped"
        assert got.size[0] > got.size[1], "aspect ratio must be preserved"

    def test_vector_pdf_still_renders_at_300_dpi(self, tmp_path):
        import pymupdf
        from PIL import Image
        import io as _io
        src = tmp_path / "page.pdf"
        d = pymupdf.open()
        d.new_page(width=595, height=842)      # A4 at 72 dpi
        d.save(src)
        d.close()

        got = Image.open(_io.BytesIO(self._extractor()._pdf_to_image(src)))
        # 595pt at 300 dpi ~= 2479 px
        assert 2400 < got.size[0] < 2560, f"expected a 300-DPI render, got {got.size}"


def test_json_object_response_format_is_always_requested(make_extractor):
    ex, client, pdf = make_extractor([VALID])
    ex.extract(pdf, "invoice")
    assert client.calls[0]["response_format"] == {"type": "json_object"}


class TestStrayEscapes:
    """
    The model over-escapes quotes: it writes `14\\\\"` in the JSON, json.loads
    turns that into a literal backslash plus a quote, and the value comes out
    as `Dizüstü Bilgisayar 14\\" i5` against a page that prints
    `Dizüstü Bilgisayar 14" i5`. Two misses in one synthetic run.

    This is FORMAT NORMALISATION -- it changes how a character is represented,
    never which characters the page carries. It must never grow into deriving
    a value; P0-4 and P0-7 are what that costs.
    """

    def _strip(self, obj):
        from src.extraction.llm_extractor import LLMExtractor
        return LLMExtractor._strip_stray_escapes(obj)

    def test_the_two_real_cases(self):
        assert self._strip('Dizüstü Bilgisayar 14\\" i5') == 'Dizüstü Bilgisayar 14" i5'
        assert self._strip("Beyaz Tahta Kalemi (4\\'lü)") == "Beyaz Tahta Kalemi (4'lü)"

    def test_it_recurses_into_items(self):
        d = {"items": [{"description": 'A4 15\\" Kağıt', "total": 5}],
             "vendor_name": "Bilir \\'İhsanoğlu\\' Şti."}
        out = self._strip(d)
        assert out["items"][0]["description"] == 'A4 15" Kağıt'
        assert out["vendor_name"] == "Bilir 'İhsanoğlu' Şti."
        assert out["items"][0]["total"] == 5

    def test_a_backslash_that_is_not_before_a_quote_is_left_alone(self):
        # only the over-escaping signature is touched, nothing else
        assert self._strip("C:\\path\\to") == "C:\\path\\to"
        assert self._strip("50\\50 karışım") == "50\\50 karışım"

    def test_non_strings_pass_through(self):
        assert self._strip(20.83) == 20.83
        assert self._strip(None) is None
        assert self._strip(True) is True

    def test_it_is_applied_by_the_parser(self, make_extractor):
        import json as _json
        payload = _json.dumps({"invoice_number": "INV-1",
                               "items": [{"description": 'Ekran 27\\" IPS'}]})
        ex, _client, pdf = make_extractor([payload])
        got = ex.extract(pdf, "invoice")
        assert got["items"][0]["description"] == 'Ekran 27" IPS'
