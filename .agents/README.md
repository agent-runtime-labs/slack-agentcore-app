# .agents Directory

This directory contains agent configuration and skills for OpenAI Codex CLI.

## Structure

```
.agents/
  config.toml     # Ruflo-generated configuration reference
  skills/         # Skill definitions
    skill-name/
      SKILL.md    # Skill instructions
      scripts/    # Optional scripts
      docs/       # Optional documentation
  README.md       # This file
```

## Configuration

The generated `config.toml` is a Ruflo configuration reference. Codex does not
automatically load `.agents/config.toml` as its project configuration. Codex reads
`~/.codex/config.toml` and trusted project `.codex/config.toml` files; keep personal
model, approval, sandbox, and MCP settings there.

Register Ruflo with Codex separately:

```bash
codex mcp add ruflo -- npx -y ruflo@latest mcp start
```

Restart Codex after registration and use `/mcp` to verify the connection.

## Skills

Skills are invoked using `$skill-name` syntax. Each skill has:
- YAML frontmatter with metadata
- Trigger and skip conditions
- Commands and examples

## Documentation

- Main instructions: `AGENTS.md` (project root)
- Local overrides: `.codex/AGENTS.override.md` (gitignored)
- Ruflo: https://github.com/ruvnet/ruflo
