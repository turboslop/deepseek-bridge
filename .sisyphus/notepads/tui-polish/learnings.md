
## T2: Editable Config Screen

**Completed**: Replaced read-only `Static` config display with editable `Input` widgets.

**Key design decisions**:
- Used `CONFIG_FIELDS` list of tuples for declarative mapping between display key, dataclass attribute, label, and category
- Fields grouped by category (Model, Network, Storage) using `Vertical` containers with `border_title`
- `dataclasses.replace()` used for immutable update of frozen `ProxyConfig`
- Boolean fields accept flexible input: `true/false`, `1/0`, `yes/no`, `on/off`
- Special handling for `log_dir` (empty string -> None) and numeric fields (`port` as int, `request_timeout` as float)
- No emojis in any labels or status messages

**Verification**: All 132 tests pass (1 skipped, same as before).
