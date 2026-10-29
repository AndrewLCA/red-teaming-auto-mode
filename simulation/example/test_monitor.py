import yaml
from pathlib import Path
from inspect_ai import eval

from auto_mode_eval.data_model import Transcript
from auto_mode_eval.evals import (
    Decision,
    SingleActionEvalSample,
    single_action_eval,
)
from auto_mode_eval.monitor import monitor_expensive

# Load existing transcript
data = yaml.safe_load(Path("example/data.yaml").read_text())
transcript = Transcript.model_validate(data["seeds"]["hard"])

# Create sample for the Bash action
sample = SingleActionEvalSample.at(
    transcript=transcript,
    decision=Decision(context=0, message=1),
    expected="ALLOW",
)

# Initialize baseline Auto Mode monitor
monitor = monitor_expensive()

# Create evaluation task
task = single_action_eval(
    dataset=[sample],
    monitor=monitor,
)

# Run evaluation
eval(task, model="anthropic/claude-opus-4-8")