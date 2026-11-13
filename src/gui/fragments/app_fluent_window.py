from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette, QResizeEvent
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import (
    FluentStyleSheet,
    FluentTitleBar,
    NavigationItemPosition,
    NavigationWidget,
    qrouter,
)
from qfluentwidgets.common.style_sheet import setCustomStyleSheet  # type: ignore
from qfluentwidgets.window.fluent_window import FluentWindowBase

from src.gui.components.qfluent_widget.no_animated_stacked_widget import (
    NoAnimatedStackedWidget,
)
from src.gui.fragments.sidebar import Sidebar


class AppFluentWindow(FluentWindowBase):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.stackedWidget = NoAnimatedStackedWidget()
        FluentStyleSheet.FLUENT_WINDOW.apply(self.stackedWidget)

        self.setTitleBar(FluentTitleBar(self))
        self.titleBar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self._set_dark_background(self.titleBar)
        setCustomStyleSheet(
            self.titleBar,
            "FluentTitleBar { background-color: #202020; }",
            "FluentTitleBar { background-color: #202020; }",
        )

        self.navigationInterface = Sidebar(self)
        self._set_dark_background(self.navigationInterface)
        self._set_dark_background(self.navigationInterface.panel)
        setCustomStyleSheet(
            self.navigationInterface.panel,
            "SidebarPanel[menu=false] { background-color: #202020; }",
            "SidebarPanel[menu=false] { background-color: #202020; }",
        )
        self.widgetLayout = QVBoxLayout()

        self.hBoxLayout.addWidget(self.navigationInterface)
        self.hBoxLayout.addLayout(self.widgetLayout)
        self.hBoxLayout.setStretchFactor(self.widgetLayout, 1)

        self.widgetLayout.addWidget(self.stackedWidget)
        self.widgetLayout.setContentsMargins(0, 48, 0, 0)

        self.navigationInterface.displayModeChanged.connect(self.titleBar.raise_)
        self.titleBar.raise_()

    @staticmethod
    def _set_dark_background(widget: QWidget) -> None:
        palette = widget.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(32, 32, 32))
        widget.setPalette(palette)
        widget.setBackgroundRole(QPalette.ColorRole.Window)
        widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        widget.setAutoFillBackground(True)

    def addWidget(
        self,
        interface: QWidget,
        navigation_widget: NavigationWidget,
        position: NavigationItemPosition = NavigationItemPosition.TOP,
        parent: QWidget | None = None,
        isTransparent: bool = False,
    ) -> None:
        """The interface must already have an object name."""
        if not interface.objectName():
            raise ValueError("The object name of `interface` can't be empty string.")
        if parent and not parent.objectName():
            raise ValueError("The object name of `parent` can't be empty string.")

        interface.setProperty("isStackedTransparent", isTransparent)
        self.stackedWidget.addWidget(interface)

        routeKey = interface.objectName()
        self.navigationInterface.addWidget(
            routeKey=routeKey,
            widget=navigation_widget,
            onClick=lambda: self.switchTo(interface),
            position=position,
            parentRouteKey=parent.objectName() if parent else None,
        )

        if self.stackedWidget.count() == 1:
            self.stackedWidget.currentChanged.connect(self._onCurrentInterfaceChanged)
            self.navigationInterface.setCurrentItem(routeKey)
            qrouter.setDefaultRouteKey(self.stackedWidget, routeKey)

        if self.stackedWidget.currentWidget() is interface:
            self._updateStackedBackground()

    def removeWidget(self, routeKey: str, interface: QWidget) -> None:
        self.navigationInterface.panel.removeWidget(routeKey)
        self.stackedWidget.removeWidget(interface)
        self._updateStackedBackground()

    def resizeEvent(self, a0: QResizeEvent | None) -> None:
        self.titleBar.move(46, 0)
        self.titleBar.resize(self.width() - 46, self.titleBar.height())
