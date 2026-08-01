# slopgen package

**EN:** Top-level package. Subpackages: `cli` (Typer entrypoint and the parked-run commands), `tui` (Textual interface), `config` (TOML config models/loader), `llm` (one client for any OpenAI-compatible provider, plus the task-specific prompt modules), `media` (ffmpeg, stock providers, free AI generators), `pipeline` (orchestrator, checkpointing, breakpoints and the stages), `publish` (upload backends).

**RU:** Корневой пакет. Подпакеты: `cli` (входная точка Typer и команды для застывших прогонов), `tui` (интерфейс Textual), `config` (модели/загрузчик TOML-конфигов), `llm` (один клиент для любого OpenAI-совместимого провайдера плюс модули промптов под конкретные задачи), `media` (ffmpeg, сток-провайдеры, бесплатные ИИ-генераторы), `pipeline` (оркестратор, чекпойнты, брейкпоинты и стадии), `publish` (бэкенды загрузки).
