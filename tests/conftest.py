"""Общая страховка для всех тестов."""

import pytest

from rfid import workdir


@pytest.fixture(autouse=True)
def no_remembered_home(monkeypatch):
    """Не читать и не писать настоящий реестр.

    Там лежит папка, выбранная в окне программы на этой машине, — с живой
    базой. Тест «чужой папки» иначе нашёл бы её и работал с ней.
    """
    monkeypatch.setattr(workdir, "remembered", lambda: None)
    monkeypatch.setattr(workdir, "remember", lambda folder: None)
