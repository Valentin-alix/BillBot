from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QEnterEvent, QIcon, QMouseEvent, QPainterPath, QResizeEvent
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QWidget
from qfluentwidgets import FluentIconBase, isDarkTheme
from qfluentwidgets.common.icon import FluentIcon as FIF
from qfluentwidgets.common.router import qrouter
from qfluentwidgets.common.style_sheet import FluentStyleSheet
from qfluentwidgets.components.material.acrylic_flyout import (
    AcrylicFlyout,
    AcrylicFlyoutViewBase,
)
from qfluentwidgets.components.navigation import (
    NavigationItemPosition,
    NavigationTreeWidget,
    NavigationWidget,
)
from qfluentwidgets.components.navigation.navigation_panel import (
    NavigationDisplayMode,
    NavigationFlyoutMenu,
    NavigationItem,
    NavigationItemLayout,
    NavigationToolTipFilter,
    NavigationTreeWidgetBase,
)
from qfluentwidgets.components.navigation.navigation_widget import NavigationToolButton
from qfluentwidgets.components.widgets.acrylic_label import AcrylicBrush
from qfluentwidgets.components.widgets.flyout import (
    Flyout,
    FlyoutAnimationType,
    FlyoutViewBase,
    SlideRightFlyoutAnimationManager,
)
from qfluentwidgets.components.widgets.tool_tip import ToolTipFilter

from src.gui.components.qfluent_widget.no_animated_scroll_area import (
    NoAnimatedScrollArea,
)


class _TopBarToolButton(NavigationToolButton):
    def enterEvent(self, event: QEnterEvent | None) -> None:
        self.isEnter = False
        self.update()


class SidebarPanel(QFrame):
    displayModeChanged = pyqtSignal(NavigationDisplayMode)

    def __init__(self, parent: QWidget, isMinimalEnabled: bool = False) -> None:
        super().__init__(parent=parent)
        self._parent = parent
        self._isMenuButtonVisible = True
        self._isReturnButtonVisible = False
        self._isCollapsible = True
        self._isAcrylicEnabled = False

        self.acrylicBrush = AcrylicBrush(self, 30)

        self.scrollArea = NoAnimatedScrollArea(self)
        self.scrollWidget = QWidget(self)

        self.menuButton = _TopBarToolButton(FIF.MENU, self)
        self.returnButton = _TopBarToolButton(FIF.RETURN, self)

        self.vBoxLayout = NavigationItemLayout(self)
        self.topLayout = NavigationItemLayout()
        self.bottomLayout = NavigationItemLayout()
        self.scrollLayout = NavigationItemLayout(self.scrollWidget)

        self.items: dict[str, NavigationItem] = {}
        self.history = qrouter

        self.expandAni = QPropertyAnimation(self, b"geometry", self)
        self.minimumExpandWidth = 1

        self.isMinimalEnabled = isMinimalEnabled
        if isMinimalEnabled:
            self.displayMode = NavigationDisplayMode.MINIMAL
        else:
            self.displayMode = NavigationDisplayMode.COMPACT

        self.__initWidget()

    def __initWidget(self) -> None:
        self.resize(48, self.height())
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        window = self.window()
        assert window
        window.installEventFilter(self)

        self.returnButton.hide()
        self.returnButton.setDisabled(True)

        self.scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scrollArea.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        hscroll_bar = self.scrollArea.horizontalScrollBar()
        assert hscroll_bar
        hscroll_bar.setEnabled(True)
        self.scrollArea.setWidget(self.scrollWidget)
        self.scrollArea.setWidgetResizable(True)

        self.expandAni.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.expandAni.setDuration(150)

        self.menuButton.clicked.connect(self.toggle)
        self.expandAni.finished.connect(self._onExpandAniFinished)
        self.history.emptyChanged.connect(self.returnButton.setDisabled)
        self.returnButton.clicked.connect(self.history.pop)

        self.returnButton.installEventFilter(ToolTipFilter(self.returnButton, 0))
        self.returnButton.setToolTip(self.tr("Retour"))

        self.menuButton.installEventFilter(ToolTipFilter(self.menuButton, 0))
        self.menuButton.setToolTip(self.tr("Open navigation"))

        self.setProperty("menu", False)
        self.scrollWidget.setObjectName("scrollWidget")
        FluentStyleSheet.NAVIGATION_INTERFACE.apply(self)
        FluentStyleSheet.NAVIGATION_INTERFACE.apply(self.scrollWidget)
        self.__initLayout()

    def __initLayout(self) -> None:
        self.vBoxLayout.setContentsMargins(0, 5, 0, 5)
        self.topLayout.setContentsMargins(4, 0, 4, 0)
        self.bottomLayout.setContentsMargins(4, 0, 4, 0)
        self.scrollLayout.setContentsMargins(4, 0, 4, 0)
        self.vBoxLayout.setSpacing(4)
        self.topLayout.setSpacing(4)
        self.bottomLayout.setSpacing(4)
        self.scrollLayout.setSpacing(4)

        self.vBoxLayout.addLayout(self.topLayout, 0)
        self.vBoxLayout.addWidget(self.scrollArea, 1)
        self.vBoxLayout.addLayout(self.bottomLayout, 0)

        self.vBoxLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.topLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scrollLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.bottomLayout.setAlignment(Qt.AlignmentFlag.AlignBottom)

        self.topLayout.addWidget(self.returnButton, 0, Qt.AlignmentFlag.AlignTop)
        self.topLayout.addWidget(self.menuButton, 0, Qt.AlignmentFlag.AlignTop)

    def _updateAcrylicColor(self) -> None:
        if isDarkTheme():
            tintColor = QColor(32, 32, 32, 200)
            luminosityColor = QColor(0, 0, 0, 0)
        else:
            tintColor = QColor(255, 255, 255, 180)
            luminosityColor = QColor(255, 255, 255, 0)

        self.acrylicBrush.tintColor = tintColor
        self.acrylicBrush.luminosityColor = luminosityColor

    def widget(self, routeKey: str) -> NavigationWidget:
        if routeKey not in self.items:
            raise ValueError(f"`{routeKey}` is illegal.")

        return self.items[routeKey].widget

    def addItem(
        self,
        routeKey: str,
        icon: str | QIcon | FluentIconBase,
        text: str,
        onClick: Callable[[], None] | None = None,
        selectable: bool = True,
        position: NavigationItemPosition = NavigationItemPosition.TOP,
        tooltip: str | None = None,
        parentRouteKey: str | None = None,
    ) -> NavigationTreeWidget | None:
        return self.insertItem(
            -1,
            routeKey,
            icon,
            text,
            onClick,
            selectable,
            position,
            tooltip,
            parentRouteKey,
        )

    def addWidget(
        self,
        routeKey: str,
        widget: NavigationWidget,
        onClick: Callable[[], None] | None = None,
        position: NavigationItemPosition = NavigationItemPosition.TOP,
        tooltip: str | None = None,
        parentRouteKey: str | None = None,
    ) -> None:
        self.insertWidget(
            -1,
            routeKey,
            widget,
            onClick,
            position,
            tooltip,
            parentRouteKey,
        )

    def insertItem(
        self,
        index: int,
        routeKey: str,
        icon: str | QIcon | FluentIconBase,
        text: str,
        onClick: Callable[[], None] | None = None,
        selectable: bool = True,
        position: NavigationItemPosition = NavigationItemPosition.TOP,
        tooltip: str | None = None,
        parentRouteKey: str | None = None,
    ) -> NavigationTreeWidget | None:
        if routeKey in self.items:
            return

        w = NavigationTreeWidget(icon, text, selectable, self)
        self.insertWidget(
            index,
            routeKey,
            w,
            onClick,
            position,
            tooltip,
            parentRouteKey,
        )
        return w

    def insertWidget(
        self,
        index: int,
        routeKey: str,
        widget: NavigationWidget,
        onClick: Callable[[], None] | None = None,
        position: NavigationItemPosition = NavigationItemPosition.TOP,
        tooltip: str | None = None,
        parentRouteKey: str | None = None,
    ) -> None:
        if routeKey in self.items:
            return

        self._registerWidget(routeKey, parentRouteKey, widget, onClick, tooltip)
        if parentRouteKey:
            parent_widget = self.widget(parentRouteKey)
            if not isinstance(parent_widget, NavigationTreeWidgetBase):
                raise TypeError(f"Navigation parent `{parentRouteKey}` must be a tree widget")
            parent_widget.insertChild(index, widget)
        else:
            self._insertWidgetToLayout(index, widget, position)

    def _registerWidget(
        self,
        routeKey: str,
        parentRouteKey: str | None,
        widget: NavigationWidget,
        onClick: Callable[[], None] | None,
        tooltip: str | None,
    ) -> None:
        widget.clicked.connect(self._onWidgetClicked)

        if onClick is not None:
            widget.clicked.connect(onClick)

        widget.setProperty("routeKey", routeKey)
        widget.setProperty("parentRouteKey", parentRouteKey)
        self.items[routeKey] = NavigationItem(routeKey, parentRouteKey, widget)

        if self.displayMode in [
            NavigationDisplayMode.EXPAND,
            NavigationDisplayMode.MENU,
        ]:
            widget.setCompacted(False)

        if tooltip:
            widget.setToolTip(tooltip)
            widget.installEventFilter(NavigationToolTipFilter(widget, 0))

    def _insertWidgetToLayout(
        self, index: int, widget: NavigationWidget, position: NavigationItemPosition
    ) -> None:
        if position == NavigationItemPosition.TOP:
            widget.setParent(self)
            self.topLayout.insertWidget(index, widget, 0, Qt.AlignmentFlag.AlignTop)
        elif position == NavigationItemPosition.SCROLL:
            widget.setParent(self.scrollWidget)
            self.scrollLayout.insertWidget(index, widget, 0, Qt.AlignmentFlag.AlignTop)
        else:
            widget.setParent(self)
            self.bottomLayout.insertWidget(index, widget, 0, Qt.AlignmentFlag.AlignBottom)

        widget.show()

    def removeWidget(self, routeKey: str) -> None:
        if routeKey not in self.items:
            return

        item = self.items.pop(routeKey)

        if item.parentRouteKey:
            parent_widget = self.widget(item.parentRouteKey)
            if not isinstance(parent_widget, NavigationTreeWidgetBase):
                raise TypeError(f"Navigation parent `{item.parentRouteKey}` must be a tree widget")
            parent_widget.removeChild(item.widget)

        if isinstance(item.widget, NavigationTreeWidgetBase):
            for child in item.widget.findChildren(
                NavigationWidget, options=Qt.FindChildOption.FindChildrenRecursively
            ):
                key = child.property("routeKey")
                if key is None:
                    continue

                self.items.pop(key)
                child.deleteLater()
                self.history.remove(key)

        item.widget.deleteLater()
        self.history.remove(routeKey)

    def setMenuButtonVisible(self, isVisible: bool) -> None:
        self._isMenuButtonVisible = isVisible
        self.menuButton.setVisible(isVisible)

    def setReturnButtonVisible(self, isVisible: bool) -> None:
        self._isReturnButtonVisible = isVisible
        self.returnButton.setVisible(isVisible)

    def setCollapsible(self, on: bool) -> None:
        self._isCollapsible = on
        if not on and self.displayMode != NavigationDisplayMode.EXPAND:
            self.expand(False)

    def setExpandWidth(self, width: int) -> None:
        if width <= 42:
            return

        self.expandWidth = width
        NavigationWidget.EXPAND_WIDTH = width - 10

    def setAcrylicEnabled(self, isEnabled: bool) -> None:
        if isEnabled == self.isAcrylicEnabled():
            return

        self._isAcrylicEnabled = isEnabled
        self.setProperty("transparent", self._canDrawAcrylic())
        self.setStyle(QApplication.style())
        self.update()

    def isAcrylicEnabled(self) -> bool:
        return self._isAcrylicEnabled

    def expand(self, useAni: bool = True) -> None:
        self._setWidgetCompacted(False)
        self.expandAni.setProperty("expand", True)
        self.menuButton.setToolTip(self.tr("Close navigation"))

        expandWidth = self.minimumExpandWidth + self.expandWidth - 322
        window = self.window()
        assert window
        if (window.width() >= expandWidth and not self.isMinimalEnabled) or not self._isCollapsible:
            self.displayMode = NavigationDisplayMode.EXPAND
        else:
            self.setProperty("menu", True)
            self.setStyle(QApplication.style())
            self.displayMode = NavigationDisplayMode.MENU

            if self._canDrawAcrylic():
                self.acrylicBrush.grabImage(
                    QRect(
                        self.mapToGlobal(QPoint()),
                        QSize(self.expandWidth, self.height()),
                    )
                )

            if not self._parent.isWindow():
                parent = self.parentWidget()
                assert parent
                pos = parent.pos()
                self.setParent(self.window())
                self.move(pos)

            self.show()

        if useAni:
            self.displayModeChanged.emit(self.displayMode)
            self.expandAni.setStartValue(QRect(self.pos(), QSize(48, self.height())))
            self.expandAni.setEndValue(QRect(self.pos(), QSize(self.expandWidth, self.height())))
            self.expandAni.start()
        else:
            self.resize(self.expandWidth, self.height())
            self._onExpandAniFinished()

    def collapse(self) -> None:
        if self.expandAni.state() == QAbstractAnimation.State.Running:
            return

        for item in self.items.values():
            w = item.widget
            if isinstance(w, NavigationTreeWidgetBase) and w.isRoot():
                w.setExpanded(False)

        self.expandAni.setStartValue(QRect(self.pos(), QSize(self.width(), self.height())))
        self.expandAni.setEndValue(QRect(self.pos(), QSize(48, self.height())))
        self.expandAni.setProperty("expand", False)
        self.expandAni.start()

        self.menuButton.setToolTip(self.tr("Open navigation"))

    def toggle(self) -> None:
        if self.displayMode in [
            NavigationDisplayMode.COMPACT,
            NavigationDisplayMode.MINIMAL,
        ]:
            self.expand()
        else:
            self.collapse()

    def setCurrentItem(self, routeKey: str) -> None:
        if routeKey not in self.items:
            return

        for k, item in self.items.items():
            item.widget.setSelected(k == routeKey)

    def _onWidgetClicked(self) -> None:
        widget = self.sender()
        if not isinstance(widget, NavigationWidget):
            return
        if not widget.isSelectable:
            if not isinstance(widget, NavigationTreeWidget):
                return
            return self._showFlyoutNavigationMenu(widget)

        self.setCurrentItem(widget.property("routeKey"))

        isLeaf = not isinstance(widget, NavigationTreeWidgetBase) or widget.isLeaf()
        if self.displayMode == NavigationDisplayMode.MENU and isLeaf:
            self.collapse()
        elif self.isCollapsed() and isinstance(widget, NavigationTreeWidget):
            self._showFlyoutNavigationMenu(widget)

    def _showFlyoutNavigationMenu(self, widget: NavigationTreeWidget) -> None:
        if not (self.isCollapsed() and isinstance(widget, NavigationTreeWidget)):
            return

        if not widget.isRoot() or widget.isLeaf():
            return

        layout = QHBoxLayout()

        if self._canDrawAcrylic():
            view = AcrylicFlyoutViewBase()
            view.setLayout(layout)
            flyout = AcrylicFlyout(view, self.window())
        else:
            view = FlyoutViewBase()
            view.setLayout(layout)
            flyout = Flyout(view, self.window())

        menu = NavigationFlyoutMenu(widget, view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(menu)

        flyout.resize(flyout.sizeHint())
        pos = SlideRightFlyoutAnimationManager(flyout).position(widget)
        flyout.exec(pos, FlyoutAnimationType.SLIDE_RIGHT)

        menu.expanded.connect(lambda: self._adjustFlyoutMenuSize(flyout, widget, menu))

    def _adjustFlyoutMenuSize(
        self, flyout: Flyout, widget: NavigationTreeWidget, menu: NavigationFlyoutMenu
    ) -> None:
        flyout.view.setFixedSize(menu.size())
        layout = flyout.layout()
        assert layout
        flyout.setFixedSize(layout.sizeHint())

        pos = flyout.aniManager.position(widget)

        window = self.window()
        assert window
        rect = window.geometry()
        w, h = flyout.sizeHint().width() + 5, flyout.sizeHint().height()
        x: int = max(rect.left(), min(pos.x(), rect.right() - w))
        y: int = max(rect.top() + 42, min(pos.y() - 4, rect.bottom() - h + 5))
        flyout.move(x, y)

    def isCollapsed(self) -> bool:
        return self.displayMode == NavigationDisplayMode.COMPACT

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        if a0 is not self.window() or not self._isCollapsible:
            return super().eventFilter(a0, a1)

        assert a1

        if a1.type() == QEvent.Type.MouseButtonRelease:
            if not isinstance(a1, QMouseEvent):
                return super().eventFilter(a0, a1)

            if (
                not self.geometry().contains(a1.position().toPoint())
                and self.displayMode == NavigationDisplayMode.MENU
            ):
                self.collapse()

        elif a1.type() == QEvent.Type.Resize:
            if not isinstance(a1, QResizeEvent):
                return super().eventFilter(a0, a1)
            w = a1.size().width()

            if w < self.minimumExpandWidth and self.displayMode == NavigationDisplayMode.EXPAND:
                self.collapse()

            elif (
                w >= self.minimumExpandWidth
                and self.displayMode == NavigationDisplayMode.COMPACT
                and not self._isMenuButtonVisible
            ):
                self.expand()

        return super().eventFilter(a0, a1)

    def _onExpandAniFinished(self) -> None:
        if not self.expandAni.property("expand"):
            if self.isMinimalEnabled:
                self.displayMode = NavigationDisplayMode.MINIMAL
            else:
                self.displayMode = NavigationDisplayMode.COMPACT

            self.displayModeChanged.emit(self.displayMode)

        if self.displayMode == NavigationDisplayMode.MINIMAL:
            self.hide()
            self.setProperty("menu", False)
            self.setStyle(QApplication.style())
        elif self.displayMode == NavigationDisplayMode.COMPACT:
            self.setProperty("menu", False)
            self.setStyle(QApplication.style())

            for item in self.items.values():
                item.widget.setCompacted(True)

            if not self._parent.isWindow():
                self.setParent(self._parent)
                self.move(0, 0)
                self.show()

    def _setWidgetCompacted(self, isCompacted: bool) -> None:
        for item in self.findChildren(NavigationWidget):
            item.setCompacted(isCompacted)

    def _canDrawAcrylic(self) -> bool:
        return self.acrylicBrush.isAvailable() and self.isAcrylicEnabled()

    def paintEvent(self, a0: Any) -> None:
        if not self._canDrawAcrylic() or self.displayMode != NavigationDisplayMode.MENU:
            return super().paintEvent(a0)

        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)
        path.addRoundedRect(0, 1, self.width() - 1, self.height() - 1, 7, 7)
        path.addRect(0, 1, 8, self.height() - 1)
        self.acrylicBrush.setClipPath(path)

        self._updateAcrylicColor()
        self.acrylicBrush.paint()

        super().paintEvent(a0)
