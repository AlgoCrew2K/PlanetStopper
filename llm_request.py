"""Request helper stub: the import signature and nothing else."""

NO_TOOL_CALL_ATTEMPTS = 0


class Reply:
    def __init__(self, reason=None, model=""):
        self.reason, self.model = reason, model

    def text(self):
        return ""

    def tool_input(self):
        return None

    def reason_text(self):
        return ""


def send(client, knob, **kwargs):
    raise NotImplementedError("Not implemented")


def strict_tool(tool):
    raise NotImplementedError("Not implemented")
