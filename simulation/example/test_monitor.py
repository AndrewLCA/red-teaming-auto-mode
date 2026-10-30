import yaml
from pathlib import Path
from inspect_ai import eval
from auto_mode_eval.data_model import Transcript
from auto_mode_eval.evals import (
    Decision,
    SingleActionEvalSample,
    single_action_eval,
)
from auto_mode_eval.monitor import monitor_expensive, monitor_original

data = yaml.safe_load(Path("example/data.yaml").read_text())
transcript = Transcript.model_validate(data["seeds"]["hard"])

sample = SingleActionEvalSample.at(
    transcript=transcript,
    decision=Decision(context=0, message=1),
    expected="ALLOW",
)

#print(sample.metadata["transcript"])
m
#monitor = monitor_expensive() #baseline with all edits classified 
monitor = monitor_original() #standard two layer reasoning in auto mode production 

task = single_action_eval(
    dataset=[sample],
    monitor=monitor,
)

eval(task, model="anthropic/claude-opus-4-8")