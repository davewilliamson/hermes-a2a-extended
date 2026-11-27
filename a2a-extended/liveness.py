"""Pure monotonic deadline bookkeeping; transport bytes are not task progress."""


class Deadlines:
    def __init__(self, now, overall, acknowledgement=30, silence=17):
        self.transport = now + silence
        self.silence = silence
        self.ack_window = acknowledgement
        self.overall_window = overall
        self.ack = self.overall = float("inf")
        self.discovery = now + 30
        self.acknowledged = self.working = False

    def activity(self, now):
        self.transport = now + self.silence

    def dispatch(self, now):
        self.overall = now + self.overall_window
        self.discovery = float("inf")

    def headers(self, now):
        self.ack = now + self.ack_window

    def state(self, now, state):
        if state in ("TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"):
            self.acknowledged = True
        if state == "TASK_STATE_WORKING" and not self.working:
            self.working = True
            self.overall = now + self.overall_window

    def next(self):
        deadlines = [(self.transport, "transport"), (self.discovery, "discovery"), (self.overall, "overall")]
        if not self.acknowledged:
            deadlines.append((self.ack, "acknowledgement"))
        return min(deadlines, key=lambda item: item[0])
