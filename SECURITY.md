# Reporting a security problem

Please do not open a public issue for a vulnerability.

- Use **Security → Report a vulnerability** on this repository (GitHub's private reporting), or
- write to hello@memvine.dev with "security" in the subject.

Say what you found, how to reproduce it, and which part it concerns (the API in `server/`, the
console, a plugin, a client library, or the hosted service at memvine.dev). You will get an answer
within five working days. Memvine is maintained by one person, so a fix can take longer than that;
you will be told what is planned and when, and credited in the changelog if you want to be.

In scope: this repository's code and the hosted service at `api.memvine.dev` and `app.memvine.dev`.
Please test against your own server (`deploy/README.md`) or your own project on the hosted service,
never against another team's data, and do not run load tests against the hosted service.

What Memvine protects and how, what an operator has to configure, and the known limits are in
[docs/security.md](docs/security.md).
