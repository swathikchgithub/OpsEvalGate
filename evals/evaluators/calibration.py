"""evals/evaluators/calibration.py - Inter-annotator agreement and Cohen's Kappa calculator."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class CalibrationReport:
  cohens_kappa: float
  observed_agreement: float
  expected_agreement: float
  sample_size: int
  is_calibrated: bool  # True if kappa >= threshold
  threshold: float
  confusion_matrix: Dict[str, Dict[str, int]]
  interpretation: str

  def to_dict(self) -> Dict[str, Any]:
    return asdict(self)


class JudgeCalibrator:
  """Calibrates automated LLM judges against gold standard human annotations."""

  def __init__(self, target_kappa_threshold: float = 0.75):
    self.target_kappa_threshold = target_kappa_threshold

  @staticmethod
  def compute_cohens_kappa(
      human_ratings: List[int], judge_ratings: List[int]
  ) -> Tuple[float, float, float, Dict[str, Dict[str, int]]]:
    """Computes Cohen's Kappa between two raters over discrete ordinal categories."""
    if len(human_ratings) != len(judge_ratings):
      raise ValueError("Human and Judge rating lists must have identical lengths.")

    n = len(human_ratings)
    if n == 0:
      return 0.0, 0.0, 0.0, {}

    categories = sorted(list(set(human_ratings) | set(judge_ratings)))

    # 1. Build Confusion Matrix
    matrix: Dict[str, Dict[str, int]] = {
        str(c1): {str(c2): 0 for c2 in categories} for c1 in categories
    }
    for h, j in zip(human_ratings, judge_ratings):
      matrix[str(h)][str(j)] += 1

    # 2. Observed Agreement (p_o)
    agreed = sum(matrix[str(c)][str(c)] for c in categories)
    p_o = agreed / n

    # 3. Expected Chance Agreement (p_e)
    p_e = 0.0
    for c in categories:
      human_c_count = sum(matrix[str(c)][str(j)] for j in categories)
      judge_c_count = sum(matrix[str(h)][str(c)] for h in categories)
      p_e += (human_c_count / n) * (judge_c_count / n)

    # 4. Cohen's Kappa (k)
    if p_e >= 1.0:
      kappa = 1.0
    else:
      kappa = (p_o - p_e) / (1.0 - p_e)

    return round(kappa, 4), round(p_o, 4), round(p_e, 4), matrix

  @staticmethod
  def interpret_kappa(kappa: float) -> str:
    if kappa >= 0.81:
      return "Almost Perfect Agreement"
    elif kappa >= 0.61:
      return "Substantial Agreement"
    elif kappa >= 0.41:
      return "Moderate Agreement"
    elif kappa >= 0.21:
      return "Fair Agreement"
    elif kappa >= 0.0:
      return "Slight Agreement"
    return "Poor / Disagreement"

  def calibrate(
      self,
      human_ratings: List[int],
      judge_ratings: List[int],
  ) -> CalibrationReport:
    kappa, p_o, p_e, matrix = self.compute_cohens_kappa(
        human_ratings, judge_ratings
    )
    is_calibrated = kappa >= self.target_kappa_threshold
    interpretation = self.interpret_kappa(kappa)

    return CalibrationReport(
        cohens_kappa=kappa,
        observed_agreement=p_o,
        expected_agreement=p_e,
        sample_size=len(human_ratings),
        is_calibrated=is_calibrated,
        threshold=self.target_kappa_threshold,
        confusion_matrix=matrix,
        interpretation=interpretation,
    )