# TUI Polish: Config Editing, API URLs, Title, Layout

## Quick Fixes

### 1. Title: "TuiApp" → "DeepSeek Cursor Proxy"
In `app.py`, Textual's `Header()` reads `self.title` from the App class. Just add:
```python
TITLE = "DeepSeek Cursor Proxy"
```
Or override `def compose_header(self)` to show a custom title with version.

### 2. API URLs on Dashboard
The server object has `config` with `host`, `port`, `ngrok`, `upstream_base_url`, etc. The dashboard doesn't show any URLs. Add a "Connection Info" section to `dashboard.py` showing:
- `local_base_url`: `http://{host}:{port}/v1`
- `api_base_url`: `{ngrok_url}/v1` if ngrok active, else same as local
- `upstream`: `{base_url}/chat/completions`
- `Ollama`: `http://{host}:{port}/api/...`

Data is already on the server object: `server.config.host`, `server.config.port`, `server.config.upstream_base_url`, etc. The `ngrok` public URL isn't stored on the server object though — need to check if it's accessible.

Actually, looking at the code in `server.py`, the `public_url` is a local variable in `main()`, not stored on the server. But we can compute the local and API base URL from config. For the ngrok URL, we'd need to store it on the server object.

### 3. Config Editor: Read-Only → Editable
The `ConfigScreen` needs:
- Convert from `Static` display to `Input` widgets for editable values
- A "Save" button that calls `replace(config, **updates)`
- Group settings into categories (Model, Network, Storage, Performance, Ollama)
- Show current value as default in input fields
- Apply changes to the running server's config

### 4. Installation
- `uv pip install textual` if not already installed
