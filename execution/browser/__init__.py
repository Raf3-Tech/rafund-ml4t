"""Browser automation layer — AGENT_ORCHESTRATION.md Section 12/26.

Every module here uses lazy, function-local `playwright` imports rather
than a module-level import, so this package stays importable (and
unit-testable via mocks) in an environment that hasn't installed
playwright — see requirements.txt's Phase 4 section. Nothing here is
reachable without clearing execution.compliance_gate first.
"""
