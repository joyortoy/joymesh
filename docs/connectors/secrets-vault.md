# Provider API key vault (OS Keychain)

JoyMesh does **not** store OpenCode/Claude/Gemini/etc. keys in SQLite.

Use:

```bash
joymesh secrets backend
joymesh secrets import-opencode          # pull existing ~/.local/share/opencode/auth.json into Keychain
joymesh secrets list                     # names only
joymesh secrets sync-opencode            # rewrite auth.json from Keychain after restart
joymesh secrets set openai               # prompted, hidden input
joymesh secrets set anthropic
joymesh secrets set gemini
joymesh secrets set xai
eval "$(joymesh secrets export-env)"     # optional env-backed tools
```

After reboot:

```bash
joymesh secrets sync-opencode
opencode auth list
```

Keys remain in macOS Keychain (`service=joymesh.secrets`). OpenCode continues to read `auth.json`; sync rebuilds that file from Keychain so you do not re-enter keys.

## Before a task (recommended)

Ask for / ensure the key once, then reuse forever from Keychain:

```bash
joymesh secrets ensure opencode-go
joymesh secrets ensure openrouter
# ...or openai / anthropic / gemini / xai for other harnesses
joymesh secrets sync-opencode
opencode auth list
```

If `ensure` finds nothing stored, it prompts (hidden input) and writes to macOS Keychain.
After reboot, run `sync-opencode` again — you will not be asked for the key unless it is missing.
