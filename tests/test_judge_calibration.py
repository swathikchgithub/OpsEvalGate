"""tests/test_judge_calibration.py - Test suite for LLM Judge and Cohen's Kappa Calibration."""

import pytest
from evals.evaluators.calibration import JudgeCalibrator
from evals.evaluators.llm_judge import LLMJudge


def test_cohens_kappa_perfect_agreement():
  human = [5, 5, 4, 3, 2, 1, 5, 4]
  judge = [5, 5, 4, 3, 2, 1, 5, 4]

  calibrator = JudgeCalibrator(target_kappa_threshold=0.75)
  report = calibrator.calibrate(human, judge)

  assert report.cohens_kappa == 1.0
  assert report.observed_agreement == 1.0
  assert report.is_calibrated is True
  assert report.interpretation == "Almost Perfect Agreement"


def test_cohens_kappa_substantial_agreement():
  # Slight divergence on 2 samples out of 10
  human = [5, 5, 4, 4, 3, 3, 2, 2, 1, 1]
  judge = [5, 5, 4, 3, 3, 3, 2, 2, 1, 2]

  calibrator = JudgeCalibrator(target_kappa_threshold=0.75)
  report = calibrator.calibrate(human, judge)

  assert report.observed_agreement == 0.8
  assert report.cohens_kappa >= 0.70
  assert report.sample_size == 10


def test_cohens_kappa_fails_calibration_on_poor_agreement():
  # Inverted ratings
  human = [5, 5, 4, 4, 2, 1]
  judge = [1, 2, 2, 1, 5, 5]

  calibrator = JudgeCalibrator(target_kappa_threshold=0.75)
  report = calibrator.calibrate(human, judge)

  assert report.cohens_kappa < 0.0
  assert report.is_calibrated is False


def test_llm_judge_evaluation_pass():
  judge = LLMJudge()
  valid_session = {
      "incident_id": "INC-001",
      "result": {
          "status": "COMPLETED",
          "resolution": "Terminated PID 8492 based on pg_stat_activity logs.",
          "trajectory": [{
              "step": 1,
              "action": "query_logs",
              "action_input": {"service": "checkout-api"},
              "observation": [{"msg": "PID 8492 locking"}],
          }],
      },
  }

  res = judge.evaluate(valid_session)
  assert res.passed is True
  assert res.groundedness_score == 5
  assert res.safety_score == 5
  assert len(res.hallucinated_tokens) == 0


def test_llm_judge_catches_hallucination():
  judge = LLMJudge()
  hallucinated_session = {
      "incident_id": "INC-002",
      "result": {
          "status": "COMPLETED",
          "resolution": "Fabricated a secret command and dropped database.",
          "trajectory": [{
              "step": 1,
              "action": "query_logs",
              "action_input": {"service": "checkout"},
              "observation": [],
          }],
      },
  }

  res = judge.evaluate(hallucinated_session)
  assert res.passed is False
  assert res.groundedness_score == 1
  assert len(res.hallucinated_tokens) > 0