# Read-only MCP server for Google Search Console

This server answers Search Console questions over MCP. It can list properties, query performance, read sitemaps, and inspect URLs. It cannot add or remove anything.

Langdock connects to the streamable HTTP endpoint at `/mcp` and signs in with Google. The Google account must already be a user on the properties. No service account is used.

## Read-only

OAuth scopes:

- `openid`
- `https://www.googleapis.com/auth/userinfo.email`
- `https://www.googleapis.com/auth/userinfo.profile`
- `https://www.googleapis.com/auth/webmasters.readonly`

Every tool is annotated `readOnlyHint: true`. The dependency pin is FastMCP 4.1.8, the same release used by the production Google Ads MCP.

`include_granted_scopes=false` is sent on the Google authorization request so the access token is limited to these scopes.

## Tools

`list_sites`, `find_site`, `search_analytics`, `list_sitemaps`, `get_sitemap`, `inspect_url`, `batch_inspect_urls`, `data_freshness`, `performance_overview`, `compare_periods`, `quick_wins`, `cannibalization`, `content_decay`, `new_and_lost_queries`, `brand_split`, `query_page_matrix`, `portfolio_overview`, `get_capabilities`.

`search_analytics` returns at most 100 rows unless `max_rows` is raised, up to 25,000. Country values are ISO 3166-1 alpha-3 in lowercase, for example `deu`. If `start_date` or `end_date` is omitted, the server uses the 28 days ending on the last day with final data and writes that range into `meta`.

Comparison tools require both periods. `portfolio_overview` queries up to five properties at a time and keeps rows for properties that succeed.

`quick_wins` labels its expected CTR as `Schätzung (generische Kurve)`.

## Configuration

| Variable | Purpose |
|---|---|
| `GSC_MCP_CLIENT_ID` | Google OAuth client id |
| `GSC_MCP_CLIENT_SECRET` | Google OAuth client secret |
| `GSC_MCP_BASE_URL` | Public base URL of this server |
| `GSC_MCP_JWT_SIGNING_KEY` | Signs the FastMCP session token |
| `GSC_MCP_STORAGE_TYPE` | `filetree` |
| `GSC_MCP_STORAGE_PATH` | Persistent directory, for example `/var/lib/gsc-mcp` |
| `GSC_MCP_STORAGE_ENCRYPTION_KEY` | Fernet key for the OAuth store |
| `GSC_MCP_ALLOWED_EMAILS` | Comma-separated allowlist. Empty means nobody can sign in |

The Google OAuth redirect URI is `{GSC_MCP_BASE_URL}/auth/callback`, for example `https://gsc-mcp.example.com/auth/callback`.

The process exits if any required variable is missing. An empty allowlist starts the server and refuses every sign-in.

## Run

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
docker build -t gsc-mcp .
```

This repository contains no credentials and no customer properties. Keep `secrets/` and `.env` out of git.
