"""Checks for the graph and runner tests; they never observe anything."""

from preflight import IdentityRef, Section, check, ok, outcome, session_for


class Things(Section):
    items: list[str] = []


class AwsThing(Section):
    identity: IdentityRef
    name_servers: dict[str, list[str]] = {}


@check("graph.thing", section=Things)
def thing(ctx, s):
    return outcome(ok())


@check("graph.aws_thing", section=AwsThing, requires=[session_for("identity")])
def aws_thing(ctx, s):
    return outcome(ok())


class Sourced(Section):
    source: IdentityRef


@check("graph.sourced", section=Sourced, requires=[session_for("source")])
def sourced(ctx, s):
    return outcome(ok())


class Identified(Section):
    identity: IdentityRef


@check("graph.identified", section=Identified)
def identified(ctx, s):
    return outcome(ok())
