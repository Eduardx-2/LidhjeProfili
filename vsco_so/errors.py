class ErrorShow:
    
    def __init__(self, error_text: str, selenium_stderr: str | None = None):
        if not isinstance(error_text, str):
            raise TypeError("error_text debe ser un texto.")
        if not error_text.strip():
            raise ValueError("error_text es obligatorio y no puede estar vacío.")
        if selenium_stderr is not None and not isinstance(selenium_stderr, str):
            raise TypeError("selenium_stderr debe ser un texto o None.")
        self.error_text = error_text
        self.selenium_stderr = selenium_stderr

    @property
    def error_str(self) -> str:
        text = self.error_text or ""
        if self.selenium_stderr:
            return f"{text}\n{self.selenium_stderr}"
        return text

    @property
    def error_json(self) -> dict[str, str]:
        return {"keyError": self.error_str}
