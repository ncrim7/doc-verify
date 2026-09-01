"""
Hybrid verification combining rule-based and LLM-based verification.
Produces ensemble decisions with confidence-weighted aggregation.
"""
import logging
from pathlib import Path

from src.verification.rule_based_verifier import RuleBasedVerifier
from src.verification.llm_verifier import LLMVerifier

logger = logging.getLogger(__name__)


class HybridVerifier:
    """
    Ensemble verifier: combines rule-based deterministic checks with
    LLM-based semantic verification for robust hallucination detection.

    Decision matrix:
        Rules OK  + LLM OK   → ACCEPT  (high confidence)
        Rules OK  + LLM WARN → CAUTION (review recommended)
        Rules WARN + LLM OK  → CAUTION (auto-correct available)
        Rules FAIL + LLM *   → REJECT  (critical rule violation)
        Rules *   + LLM FAIL → REVIEW  (hallucination detected)
    """

    def __init__(self, provider: str = "openai", llm_weight: float = 0.6):
        """
        Args:
            provider: LLM provider for verification
            llm_weight: Weight given to LLM verification (0-1).
                        Rule weight = 1 - llm_weight
        """
        self.rule_verifier = RuleBasedVerifier()
        self.llm_verifier = LLMVerifier(provider=provider)
        self.llm_weight = llm_weight
        self.rule_weight = 1.0 - llm_weight

    def verify(self, extracted: dict, doc_type: str, pdf_path: str | Path) -> dict:
        """
        Run both verification methods and produce ensemble result.

        Returns:
            {
                'decision': 'ACCEPT' | 'CAUTION' | 'REVIEW' | 'REJECT',
                'combined_score': float (0-1),
                'rule_verification': {...},
                'llm_verification': {...},
                'issues': [...],
                'auto_corrections': {...},
                'hallucination_detected': bool,
            }
        """
        # 1. Rule-based verification (fast, deterministic)
        logger.info("Running rule-based verification...")
        rule_result = self.rule_verifier.verify(extracted, doc_type)

        # 2. LLM-based verification (slower, semantic)
        logger.info("Running LLM-based verification...")
        llm_result = self.llm_verifier.verify(extracted, pdf_path)

        # 3. Combine results
        combined = self._combine(rule_result, llm_result)

        return combined

    def verify_rules_only(self, extracted: dict, doc_type: str) -> dict:
        """Run only rule-based verification (no API call)."""
        rule_result = self.rule_verifier.verify(extracted, doc_type)
        decision = "ACCEPT" if rule_result["valid"] else "REJECT"
        if rule_result["severity_summary"]["warning"] > 0 and rule_result["valid"]:
            decision = "CAUTION"

        return {
            "decision": decision,
            "combined_score": rule_result["score"],
            "rule_verification": rule_result,
            "llm_verification": None,
            "issues": rule_result["issues"],
            "auto_corrections": rule_result["auto_corrections"],
            "hallucination_detected": False,
        }

    def _combine(self, rule_result: dict, llm_result: dict) -> dict:
        """Combine rule and LLM results into ensemble decision."""

        # Combined score
        rule_score = rule_result["score"]
        llm_score = llm_result.get("overall_confidence", 0.5)
        combined_score = round(
            self.rule_weight * rule_score + self.llm_weight * llm_score, 4
        )

        # Hallucination from LLM
        hall_detected = llm_result.get("hallucination_detected", False)
        hall_count = llm_result.get("hallucination_count", 0)

        # Collect all issues
        all_issues = list(rule_result["issues"])
        for item in llm_result.get("verification_results", []):
            if item.get("status") in ("hallucination", "error", "missing"):
                all_issues.append({
                    "rule": f"llm_{item['status']}",
                    "field": item.get("field_name", "unknown"),
                    "severity": "critical" if item["status"] == "hallucination" else "warning",
                    "message": item.get("notes", f"LLM detected {item['status']}"),
                    "source": "llm",
                })

        # Decision
        rule_critical = rule_result["severity_summary"]["critical"]
        rule_warnings = rule_result["severity_summary"]["warning"]

        if rule_critical > 0:
            decision = "REJECT"
        elif hall_detected:
            decision = "REVIEW"
        elif rule_warnings > 0 or llm_result.get("error_count", 0) > 0:
            decision = "CAUTION"
        else:
            decision = "ACCEPT"

        return {
            "decision": decision,
            "combined_score": combined_score,
            "rule_verification": rule_result,
            "llm_verification": llm_result,
            "issues": all_issues,
            "auto_corrections": rule_result["auto_corrections"],
            "hallucination_detected": hall_detected,
            "hallucination_count": hall_count,
            "summary": self._build_summary(decision, rule_result, llm_result),
        }

    def _build_summary(self, decision: str, rule_result: dict, llm_result: dict) -> str:
        parts = [f"Decision: {decision}."]

        sev = rule_result["severity_summary"]
        if sev["critical"] > 0:
            parts.append(f"{sev['critical']} critical rule violations.")
        if sev["warning"] > 0:
            parts.append(f"{sev['warning']} warnings.")

        hall = llm_result.get("hallucination_count", 0)
        err = llm_result.get("error_count", 0)
        if hall > 0:
            parts.append(f"{hall} hallucinations detected by LLM.")
        if err > 0:
            parts.append(f"{err} extraction errors detected by LLM.")

        conf = llm_result.get("overall_confidence", 0)
        parts.append(f"LLM confidence: {conf:.0%}.")

        return " ".join(parts)
