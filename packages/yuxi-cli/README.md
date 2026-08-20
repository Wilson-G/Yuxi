# yuxi-cli

ZhiForge command line client. The package, executable, and local configuration
directory keep the compatibility names `yuxi-cli`, `yuxi`, and `~/.yuxi`.

Current scope:

- remote management through `~/.yuxi/config.toml`
- browser login
- API Key import through `--api-key`
- `whoami`, `status`, and `logout`
- server discovery and compatibility check for ZhiForge `>=0.7.1`
- `yuxi chat` for a temporary local browser chat with streamed Agent output; `/state` reads thread state and `/approve` resumes a pending tool approval
- `yuxi agent eval` for running existing Langfuse dataset experiments with a logged-in remote

The `yuxi kb` command group manages legacy knowledge bases. It is available
only when the remote enables the legacy knowledge capabilities;
new expert materials enter through the ZhiForge material library instead.
