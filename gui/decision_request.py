"""Cross-process bridge for one speaker-naming decision: sent to the GUI on
a multiprocessing.Queue, fulfilled by putting the answer on a second queue
the worker process blocks on."""


class SpeakerDecisionRequest:
    def __init__(self, label, samples, decision_queue):
        self.label = label
        self.samples = samples
        self._decision_queue = decision_queue

    def fulfill(self, choice):
        self._decision_queue.put(choice)
