from collections.abc import Callable

from pydantic import ValidationError
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import QFormLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ComboBox, LineEdit, PrimaryPushButton, PushButton
from qfluentwidgets.components.dialog_box.dialog import MessageBox

from src.gui.pages.settings.settings_overview import SettingsOverview
from src.services.background import run_in_background
from src.utils.runtime_support import RuntimeSetupError, error_message


class SettingsPanel(QWidget):
    def __init__(self, description: str) -> None:
        super().__init__()
        self._deletion_handlers: dict[SettingsOverview, Callable[[str], None]] = {}
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        label = BodyLabel(description, self)
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addSpacing(16)
        self.overview_layout = QVBoxLayout()
        layout.addLayout(self.overview_layout)
        self.form = QFormLayout()
        self.form.setVerticalSpacing(12)
        self.form.setHorizontalSpacing(20)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        layout.addLayout(self.form)
        self.status = BodyLabel("", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addSpacing(20)
        buttons_layout = QHBoxLayout()
        buttons_layout.addSpacing(8)
        self.save_button = PrimaryPushButton("Save", self)
        self.cancel_button = PushButton("Cancel / refresh", self)
        buttons_layout.addWidget(self.save_button)
        buttons_layout.addSpacing(8)
        buttons_layout.addWidget(self.cancel_button)
        buttons_layout.addStretch()
        layout.addLayout(buttons_layout)
        layout.addStretch()
        self.save_button.clicked.connect(self.save)
        self.cancel_button.clicked.connect(self.reload)

    def line(self, label: str) -> LineEdit:
        widget = LineEdit(self)
        self.form.addRow(BodyLabel(label, self), widget)
        return widget

    def combo(self, label: str, items: list[str] | None = None) -> ComboBox:
        widget = ComboBox(self)
        if items:
            widget.addItems(items)
        self.form.addRow(BodyLabel(label, self), widget)
        return widget

    def overview(self, headers: list[str], new_label: str | None = None) -> SettingsOverview:
        widget = SettingsOverview(headers, self, new_label)
        self.overview_layout.addWidget(widget)
        return widget

    def bind_deletion(
        self,
        overview: SettingsOverview,
        action: Callable[[str], None],
        on_deleted: Callable[[], None] | None = None,
        *,
        background: bool = True,
    ) -> None:
        def request(identifier: str) -> None:
            confirmation = MessageBox(
                "Delete permanently",
                f"Permanently delete {identifier}? This action cannot be undone.",
                self.window(),
            )
            confirmation.yesButton.setText("Delete")
            confirmation.cancelButton.setText("Cancel")
            if not confirmation.exec():
                return

            def done(_: None) -> None:
                if on_deleted is not None:
                    on_deleted()
                self.reload()

            self.perform(lambda: action(identifier), done, background=background)

        previous = self._deletion_handlers.get(overview)
        if previous is not None:
            overview.delete_requested.disconnect(previous)
        self._deletion_handlers[overview] = request
        overview.delete_requested.connect(request)

    def perform[ResultT](
        self,
        action: Callable[[], ResultT],
        done: Callable[[ResultT], None],
        *,
        background: bool = True,
    ) -> None:
        self.setEnabled(False)
        self.status.setText("In progress…")

        def work(_: Callable[[str], None]) -> ResultT:
            try:
                return action()
            except ValidationError as error:
                raise RuntimeSetupError("Invalid configuration. Check the provided fields.") from error
            except ValueError as error:
                raise RuntimeSetupError(str(error)) from error

        def success(result: ResultT) -> None:
            self.setEnabled(True)
            self.status.setText("")
            done(result)

        def failure(error: object) -> None:
            self.setEnabled(True)
            self.status.setText(error_message(error))

        if background:
            run_in_background(work, on_success=success, on_error=failure, parent=self)
        else:
            try:
                result = work(lambda _: None)
            except RuntimeSetupError as error:
                failure(error)
            else:
                success(result)

    def showEvent(self, a0: QShowEvent | None) -> None:
        super().showEvent(a0)
        if self.isEnabled():
            self.reload()

    def reload(self) -> None:
        raise NotImplementedError

    def save(self) -> None:
        raise NotImplementedError

    def invalid(self, message: str = "Check the numeric field values.") -> None:
        self.status.setText(message)
