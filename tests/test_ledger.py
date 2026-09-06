"""
Tests for the document memory and duplicate risk.

Two fixtures carry this file, and both come from outside the code:

**The 42.000 TL case.** A real company, one month, three duplicate incidents,
two caught by a person noticing the amount and one paid. The same invoice
arrived once as an e-Arşiv PDF and once as an e-Fatura, was entered by two
people on different days under differently written document numbers, and
surfaced 25 days later when the supplier said they had been paid twice. It is
the benchmark this feature exists to pass.

**The subscription that must not fire.** real_002 and real_007 in our own
corpus: same supplier, same 20,00 USD, April and June. A rule built on "same
supplier, same amount" calls that a duplicate every month until the user stops
reading — and a person entering eighty invoices a day already presses Enter
through their ERP's warning for exactly that reason.
"""
from datetime import date

import pytest

from src.decision.engine import Basis, Verdict, decide
from src.ledger.canonical import (
    CanonicalDocument, Channel, normalise_doc_number, normalise_name,
    number_core,
)
from src.ledger.duplicate import Risk, check_duplicates, to_findings
from src.ledger.store import DocumentStore


@pytest.fixture
def store():
    s = DocumentStore()
    yield s
    s.close()


def _doc(**kw) -> CanonicalDocument:
    base = dict(supplier_tax_id="1234567890", supplier_name="ABC Lojistik A.Ş.",
                doc_number="GIB2026000012345", issue_date=date(2026, 8, 15),
                total_amount=42000.0, currency="TRY",
                channel=Channel.EFATURA)
    base.update(kw)
    return CanonicalDocument(**base)


# ---------------------------------------------------------------------------
# normalisation
# ---------------------------------------------------------------------------

class TestNormalisation:
    def test_turkish_dotted_capital_folds_to_i_not_i_plus_dot(self):
        # str.lower() maps İ to i + combining dot, which then compares unequal
        assert normalise_name("İhsanoğlu") == normalise_name("ihsanoğlu")

    def test_legal_forms_do_not_change_who_the_supplier_is(self):
        a = normalise_name("TTNET ANONİM ŞİRKETİ")
        assert a == normalise_name("TTNET A.Ş.") == normalise_name("Ttnet")

    def test_punctuation_and_case_leave_a_document_number_alone(self):
        n = normalise_doc_number("GIB-2026/000012345")
        assert n == normalise_doc_number(" gib2026000012345 ")

    def test_the_digit_core_survives_a_retype(self):
        # the case that costs money: a person types the short form from a PDF
        assert number_core("GIB2026000012345").endswith(number_core("GIB-12345"))
        assert number_core("GIB-12345") == "12345"

    def test_leading_zeros_are_not_identity(self):
        assert number_core("INV-00042") == "42"

    def test_a_tax_id_beats_a_name_as_the_supplier_key(self):
        # the only supplier field with a check digit
        d = _doc(supplier_name="yazım hatası olan ad")
        assert d.supplier_key == "1234567890"

    def test_the_name_is_used_when_there_is_no_tax_id(self):
        assert _doc(supplier_tax_id=None).supplier_key == "abc lojistik"

    def test_amounts_are_compared_in_kurus_not_floats(self):
        assert _doc(total_amount=42000.0).amount_key == 4200000
        assert _doc(total_amount=0.1 + 0.2).amount_key == 30


# ---------------------------------------------------------------------------
# the benchmark
# ---------------------------------------------------------------------------

class TestTheFortyTwoThousandCase:
    """
    Paid in real life. The question this feature has to answer is not "how
    accurately do we read a page" but **"would we have stopped this before the
    payment run?"**
    """

    def _the_pair(self):
        first = _doc(doc_number="GIB2026000012345", channel=Channel.EARSIV_PDF,
                     source_ref="mail-ekindeki-pdf")
        # same event, entered by someone else, from the other channel, typed
        # short — different string, different day, same money
        second = _doc(doc_number="GIB-12345", channel=Channel.EFATURA,
                      issue_date=date(2026, 8, 15), source_ref="entegrator")
        return first, second

    def test_it_is_caught(self, store):
        first, second = self._the_pair()
        store.record(first)
        assert check_duplicates(second, store).risk is Risk.HIGH

    def test_it_stops_the_payment(self, store):
        first, second = self._the_pair()
        store.record(first)
        findings = to_findings(check_duplicates(second, store), second)
        assert decide(findings).verdict is Verdict.HOLD

    def test_the_whole_invoice_is_not_counted_as_a_discrepancy(self, store):
        # 42.000 is money that would move twice, not a gap between two figures
        first, second = self._the_pair()
        store.record(first)
        d = decide(to_findings(check_duplicates(second, store), second))
        assert d.financial_impact_try == 0.0
        assert d.verdict is Verdict.HOLD

    def test_an_exact_ERP_string_match_would_have_missed_it(self, store):
        first, second = self._the_pair()
        assert first.number_key != second.number_key, (
            "if these matched, the ERP's own check would have caught it and "
            "there would be nothing here to build"
        )
        store.record(first)
        assert store.by_identity(second) == []      # tier 1 finds nothing
        assert store.by_number_core(second)         # tier 2 does

    def test_the_other_channel_is_named_in_the_message(self, store):
        first, second = self._the_pair()
        store.record(first)
        f = to_findings(check_duplicates(second, store), second)[0]
        assert "başka bir kanaldan" in f.message

    def test_a_plain_repeat_of_the_same_number_is_caught_too(self, store):
        first, _ = self._the_pair()
        store.record(first)
        again = _doc(doc_number=" gib-2026/000012345 ", channel=Channel.EMAIL_PDF)
        assert check_duplicates(again, store).risk is Risk.HIGH


# ---------------------------------------------------------------------------
# the false positive that would sink it
# ---------------------------------------------------------------------------

class TestTheSubscriptionThatMustNotFire:
    """
    A flag that fires every month is worse than no flag. The ERP already warns
    on a repeated document number and the warning is pressed through, because
    it fires when it should not.
    """

    def _subscription(self, store, months=(4, 5, 6)):
        for m in months:
            store.record(CanonicalDocument(
                supplier_tax_id="701236788", supplier_name="Anthropic, PBC",
                doc_number=f"1OSB6CRZ-000{m}", issue_date=date(2026, m, 13),
                total_amount=24.00, currency="USD", channel=Channel.EMAIL_PDF))

    def test_monthly_billing_never_reaches_the_weak_tier_at_all(self, store):
        """
        The first line of defence is the window, not the damper. A month is
        longer than ten days, so a subscription simply never looks like a
        near-in-time coincidence.
        """
        self._subscription(store)
        july = CanonicalDocument(
            supplier_tax_id="701236788", supplier_name="Anthropic, PBC",
            doc_number="1OSB6CRZ-0007", issue_date=date(2026, 7, 13),
            total_amount=24.00, currency="USD", channel=Channel.EMAIL_PDF)
        assert check_duplicates(july, store).matches == []

    def test_and_the_damper_catches_it_if_the_window_is_widened(self, store):
        # second line of defence: even at a month-wide window, three prior
        # documents at a steady rhythm read as a pattern rather than a repeat
        self._subscription(store)
        july = CanonicalDocument(
            supplier_tax_id="701236788", supplier_name="Anthropic, PBC",
            doc_number="1OSB6CRZ-0007", issue_date=date(2026, 7, 13),
            total_amount=24.00, currency="USD", channel=Channel.EMAIL_PDF)
        result = check_duplicates(july, store, window_days=35)
        assert result.recurring is True
        assert result.risk is Risk.LOW
        assert "abonelik" in result.recurring_note

    def test_it_does_not_stop_a_payment(self, store):
        self._subscription(store)
        july = CanonicalDocument(
            supplier_tax_id="701236788", doc_number="1OSB6CRZ-0007",
            issue_date=date(2026, 7, 13), total_amount=24.00, currency="USD")
        d = decide(to_findings(check_duplicates(july, store), july))
        assert d.verdict is Verdict.PAY

    def test_but_an_identical_number_still_fires_inside_a_subscription(self, store):
        # the damper never touches tier 1: a repeated document NUMBER is a
        # repeat whatever the rhythm
        self._subscription(store)
        same = CanonicalDocument(
            supplier_tax_id="701236788", doc_number="1OSB6CRZ-0006",
            issue_date=date(2026, 6, 13), total_amount=24.00, currency="USD")
        assert check_duplicates(same, store).risk is Risk.HIGH

    def test_two_months_are_not_yet_a_pattern(self, store):
        """
        Three prior points make a rhythm; two make a coincidence. The threshold
        is slow on purpose — damping too eagerly hides a real duplicate, which
        is what the 42.000 TL cost, while damping too late costs one queue row.
        """
        self._subscription(store, months=(4, 5))
        june = CanonicalDocument(
            supplier_tax_id="701236788", doc_number="1OSB6CRZ-0006",
            issue_date=date(2026, 6, 13), total_amount=24.00, currency="USD")
        r = check_duplicates(june, store, window_days=35)
        assert r.recurring is False and r.risk is Risk.MEDIUM


# ---------------------------------------------------------------------------
# restraint
# ---------------------------------------------------------------------------

class TestItStaysQuiet:
    def test_an_empty_ledger_finds_nothing(self, store):
        assert check_duplicates(_doc(), store).matches == []
        assert check_duplicates(_doc(), store).risk is Risk.LOW

    def test_a_different_supplier_with_the_same_amount_is_not_a_match(self, store):
        store.record(_doc())
        other = _doc(supplier_tax_id="9876543217", supplier_name="XYZ Nakliyat")
        assert check_duplicates(other, store).matches == []

    def test_the_same_amount_outside_the_window_is_not_a_match(self, store):
        store.record(_doc(issue_date=date(2026, 1, 5)))
        later = _doc(doc_number="BASKA-999", issue_date=date(2026, 8, 15))
        assert check_duplicates(later, store).matches == []

    def test_a_shared_digit_tail_without_the_amount_is_not_evidence(self, store):
        store.record(_doc(doc_number="GIB2026000012345", total_amount=42000.0))
        other = _doc(doc_number="GIB-12345", total_amount=999.0)
        assert not any(m.risk is Risk.HIGH
                       for m in check_duplicates(other, store).matches)

    def test_a_short_number_core_is_never_looked_up(self, store):
        # '5' would match half the ledger
        store.record(_doc(doc_number="A-5"))
        assert store.by_number_core(_doc(doc_number="B-5")) == []

    def test_a_document_with_no_number_and_no_amount_matches_nothing(self, store):
        store.record(_doc())
        blank = CanonicalDocument(supplier_tax_id="1234567890")
        assert check_duplicates(blank, store).matches == []


# ---------------------------------------------------------------------------
# the canonical shape
# ---------------------------------------------------------------------------

class TestOneShapeWhateverTheChannel:
    def test_xml_and_pdf_produce_the_same_identity(self):
        """
        Roughly three quarters of a real company's volume arrives as structured
        XML and must not pay for extraction it does not need — but it has to
        land in the same shape, or the checks cannot compare across channels.
        """
        from_pdf = CanonicalDocument.from_extraction(
            {"invoice_number": "GIB2026000012345", "vendor_tax_id": "1234567890",
             "vendor_name": "ABC Lojistik A.Ş.", "date": "2026-08-15",
             "total_amount": 42000.0, "currency": "TRY"},
            channel=Channel.EARSIV_PDF)
        from_xml = CanonicalDocument.from_efatura(
            {"doc_number": "GIB2026000012345", "supplier_tax_id": "1234567890",
             "supplier_name": "ABC LOJİSTİK ANONİM ŞİRKETİ",
             "issue_date": "2026-08-15", "total_amount": 42000.0,
             "currency": "TRY", "ettn": "abc-123"})
        assert from_pdf.identity_key == from_xml.identity_key
        assert from_pdf.amount_key == from_xml.amount_key
        assert from_pdf.channel != from_xml.channel      # provenance is kept

    def test_a_receipt_uses_its_own_number_field(self):
        d = CanonicalDocument.from_extraction(
            {"receipt_number": "0027", "store_name": "SARITEKELİ MARKET",
             "date": "2025-02-15", "total_amount": 335.0}, doc_type="receipt")
        # 'SARITEKELİ' folds to 'sarıtekeli': in Turkish a capital I lowercases
        # to the dotless ı, which is the whole reason names get Turkish rules
        # and document numbers do not
        assert d.doc_number == "0027" and d.supplier_key == "sarıtekeli market"

    def test_turkish_date_formats_are_understood(self):
        for s in ("2026-08-15", "15.08.2026", "15/08/2026"):
            assert CanonicalDocument.from_extraction(
                {"date": s}).issue_date == date(2026, 8, 15)

    def test_an_unreadable_date_is_none_not_a_guess(self):
        assert CanonicalDocument.from_extraction({"date": "geçen ay"}).issue_date is None


class TestStore:
    def test_it_counts_what_it_was_given(self, store):
        store.record(_doc()); store.record(_doc(doc_number="X-2"))
        assert store.count() == 2

    def test_recording_is_separate_from_checking(self, store):
        # a flagged document usually should NOT join the history, so the caller
        # decides rather than the check
        d = _doc()
        store.record(d)
        assert check_duplicates(d, store).risk is Risk.HIGH
        assert store.count() == 1, "checking must not record"

    def test_supplier_history_is_scoped_to_one_supplier(self, store):
        store.record(_doc())
        store.record(_doc(supplier_tax_id="9876543217"))
        assert len(store.supplier_history(_doc())) == 1
