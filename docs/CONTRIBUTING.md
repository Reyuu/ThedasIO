## Contributing

### Pull requests

If you would like to contribute to this project, please follow these guidelines, and ensure that your contributions adhere to the project's coding standards and practices:

- follow the existing code style and formatting conventions (see [docs/CODE_CONVENTIONS.md](docs/CODE_CONVENTIONS.md))
- write clear and concise commit messages
- keep roundtrips byte-identical: untouched files come out identical to the source, edited ones are re-encoded surgically (see [README.md](README.md) Scope)

I welcome contributions from everyone, regardless of experience level. Please feel free to submit pull requests and engage in discussions to help improve the project.

#### Before committing

```sh
uv run ruff check src tests tools
uv run pytest -m "not blender"   # fast unit tests, no Blender needed
```

### Submitting issues

When submitting issues, please provide clear and detailed information to help us understand and address the problem effectively:

- include a descriptive title and a detailed description of the issue
- provide steps to reproduce the issue, if applicable (source file, what you edited, import or export)
- include any relevant logs, screenshots and error messages
- for real-Blender failures, include the Blender version and how `BLENDER_EXE` resolves

I appreciate your efforts in reporting issues and helping improve the project.

### AI usage

Using AI to submit code or documentation contributions is allowed, but you must ensure that the output is accurate, relevant, and adheres to the project's coding standards and guidelines. Always review and verify AI-generated content before submitting it.

If I catch someone not knowing what their code does or blindly submitting AI-generated content without review, their contributions are going to be rejected and **all** of their pull requests will be closed.

### Code of Conduct

We do not have one at the moment, but we expect all contributors to behave respectfully and professionally while interacting with the project and its community.

**Remember**, modders do not owe anyone their time or effort, so always be courteous and considerate in your interactions.

Any homophobic, transphobic, racist, sexist, or otherwise discriminatory behavior will not be tolerated.
