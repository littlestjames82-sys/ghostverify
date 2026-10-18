"""Deploy helper that is deliberately NOT part of any test bundle.

Patches touching this file must be flagged by `ghostverify post`
because the file was never in the agent's context bundle.
"""


def deploy_target():
    return "local"
