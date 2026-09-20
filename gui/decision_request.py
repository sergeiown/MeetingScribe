"""Cross-process request/response bridge for one speaker-naming decision.

Built in the worker (child) process, sent to the GUI (parent) process as a
message on a multiprocessing.Queue, and fulfilled there once the user answers
a modal dialog - the answer is placed on a second queue that the child
process is blocked reading from.
"""


class SpeakerDecisionRequest:
    def __init__(self, label, samples, decision_queue):
        self.label = label
        self.samples = samples
        self._decision_queue = decision_queue

    def fulfill(self, choice):
        self._decision_queue.put(choice)
