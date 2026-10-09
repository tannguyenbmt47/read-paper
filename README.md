# Loupe

**Read English research papers in Vietnamese, without losing the argument.**

![Original, translation and explanation side by side](docs/anh/doc-ba-cot.webp)

## What it does

- **Translates a paper paragraph by paragraph**, with the original next to every
  paragraph and a plain-language explanation of what each one does in the
  argument.
- **Summarises the paper first**: problem, gap, core idea, method, evidence,
  limits, and a fixed glossary.
- **Turns the paper into a talk deck** you can edit on the slide and present in
  the browser.
- **Exports a report** as HTML, PDF or Markdown.
- **Answers questions across many papers**, citing every claim down to the
  passage.

Runs on your own machine. Models via [OpenRouter](https://openrouter.ai). A
typical paper costs **$0.04–0.10** to translate.

## Install

You need Python 3.10+ and an [OpenRouter API key](https://openrouter.ai/keys).

```bash
./run.sh     # first run creates .env, then stops
             # add your key: OPENROUTER_API_KEY=...
./run.sh     # starts the app at http://localhost:8010
```

Docker: see [DOCKER.md](DOCKER.md).

## How to use

1. **Import**: drop a PDF, paste an arXiv link, Markdown or plain text.
2. **Review** (free): check how the paper was split and cropped. The price is
   shown before anything is paid for.
3. **Translate**: choose which columns and sections you want, then press
   **Dịch**.
4. **Read**: click a figure reference to preview it, highlight and take notes,
   press 💡 to explain a paragraph, or ask questions in the chat.
5. **Present**: press **Slide**, pick a talk length, then **Tạo slide**.
6. **Export**: use the download button for HTML, PDF or Markdown.

Full guide in Vietnamese: [Hướng dẫn sử dụng](docs/huong-dan.html).

## Support

If Loupe saves you time, you can buy me a coffee.

[![Buy me a coffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-ffdd00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/tannguyenbmt47)

---

[Changelog](CHANGELOG.md) · [Architecture notes](CLAUDE.md) · Tests:
`.venv/bin/python -m pytest`
