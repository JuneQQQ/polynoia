"""Storage repository layer (package).

Split from the former 1680-LOC repo.py into per-entity submodules. The public
surface is unchanged — every name that used to live on polynoia.storage.repo is
re-exported here, so existing `from polynoia.storage import repo` /
`storage_repo.fn(...)` call sites keep working verbatim.
"""
from __future__ import annotations

from polynoia.storage.repo.agents import (
    delete_agent,
    list_agents,
    upsert_agent,
)
from polynoia.storage.repo.cleanup import (
    close_terminal_card_for_run,
    reap_orphan_burst_tasks,
    reap_orphan_terminal_cards,
    reap_orphan_tool_calls,
)
from polynoia.storage.repo.conflicts import (
    create_conflict,
    get_conflict,
    list_conflicts,
    set_conflict_status,
    update_conflict_files,
)
from polynoia.storage.repo.conv_memory import (
    add_conv_memory,
    list_agent_memory,
    list_conv_memory,
    list_workspace_memory,
)
from polynoia.storage.repo.conversations import (
    add_pin,
    clear_conversation_messages,
    create_conversation,
    delete_conversation,
    get_conversation,
    increment_unread,
    list_conversations,
    list_pins,
    reset_unread,
    set_archived,
    set_draft_attachments,
    set_draft_text,
    set_member_roles,
    set_members,
    set_merge_mode,
    set_pinned,
    set_title,
    set_workspace_id,
)
from polynoia.storage.repo.messages import (
    append_message,
    delete_message,
    delete_messages_from,
    list_messages,
    list_pinned_messages,
    set_message_pinned,
    update_message_payload,
    upsert_message,
)
from polynoia.storage.repo.onboarded_adapters import (
    add_onboarded_adapter,
    get_adapter_proxy,
    list_onboarded_adapter_rows,
    list_onboarded_adapters,
    remove_onboarded_adapter,
    set_adapter_proxy,
)
from polynoia.storage.repo.pending_access import (
    active_access_grant,
    create_pending_access,
    get_pending_access,
    list_pending_access,
    set_pending_access_status,
)
from polynoia.storage.repo.pending_edits import (
    abandon_pending_edits_for_adapter,
    create_pending_edit,
    get_pending_edit,
    has_waiting_pending_access,
    has_waiting_pending_edits,
    list_pending_edits,
    set_pending_edit_status,
)
from polynoia.storage.repo.process_runs import (
    finish_stale_blocking_processes,
    get_process_run,
    list_process_runs,
    list_running_process_runs,
    mark_process_run_killed,
    reap_stale_process_runs,
    sweep_process_liveness,
    upsert_process_run,
)
from polynoia.storage.repo.providers import (
    list_providers,
    upsert_provider,
)
from polynoia.storage.repo.servers import (
    list_servers,
    upsert_server,
)
from polynoia.storage.repo.workspaces import (
    delete_workspace,
    list_workspaces,
    upsert_workspace,
)

__all__ = [
    "abandon_pending_edits_for_adapter",
    "active_access_grant",
    "add_conv_memory",
    "add_onboarded_adapter",
    "add_pin",
    "append_message",
    "clear_conversation_messages",
    "close_terminal_card_for_run",
    "create_conflict",
    "create_conversation",
    "create_pending_access",
    "create_pending_edit",
    "delete_agent",
    "delete_conversation",
    "delete_message",
    "delete_messages_from",
    "delete_workspace",
    "finish_stale_blocking_processes",
    "get_adapter_proxy",
    "get_conflict",
    "get_conversation",
    "get_pending_access",
    "get_pending_edit",
    "get_process_run",
    "has_waiting_pending_access",
    "has_waiting_pending_edits",
    "increment_unread",
    "list_agent_memory",
    "list_agents",
    "list_conflicts",
    "list_conv_memory",
    "list_conversations",
    "list_messages",
    "list_onboarded_adapter_rows",
    "list_onboarded_adapters",
    "list_pending_access",
    "list_pending_edits",
    "list_pinned_messages",
    "list_pins",
    "list_process_runs",
    "list_providers",
    "list_running_process_runs",
    "list_servers",
    "list_workspace_memory",
    "list_workspaces",
    "mark_process_run_killed",
    "reap_orphan_burst_tasks",
    "reap_orphan_terminal_cards",
    "reap_orphan_tool_calls",
    "reap_stale_process_runs",
    "remove_onboarded_adapter",
    "reset_unread",
    "set_adapter_proxy",
    "set_archived",
    "set_conflict_status",
    "set_draft_attachments",
    "set_draft_text",
    "set_member_roles",
    "set_members",
    "set_merge_mode",
    "set_message_pinned",
    "set_pending_access_status",
    "set_pending_edit_status",
    "set_pinned",
    "set_title",
    "set_workspace_id",
    "sweep_process_liveness",
    "update_conflict_files",
    "update_message_payload",
    "upsert_agent",
    "upsert_message",
    "upsert_process_run",
    "upsert_provider",
    "upsert_server",
    "upsert_workspace",
]
