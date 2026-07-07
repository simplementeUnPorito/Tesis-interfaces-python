"""PyQt reviewer for field hammer/geophone picks."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

try:
    from .field_review_data import (
        AverageArrivalAnnotation,
        FieldDataset,
        FieldShot,
        PickAnnotation,
        annotations_signature,
        auto_pick_shot,
        build_waterfall_matrix,
        compute_average_groups,
        default_average_arrivals_path,
        default_output_dir,
        export_processed,
        format_distance_label,
        hammer_global_time_signal,
        load_annotations,
        load_average_arrivals,
        load_signal,
        save_annotations,
        save_average_arrivals,
    )
except ImportError:  # pragma: no cover - script execution from this folder
    from field_review_data import (
        AverageArrivalAnnotation,
        FieldDataset,
        FieldShot,
        PickAnnotation,
        annotations_signature,
        auto_pick_shot,
        build_waterfall_matrix,
        compute_average_groups,
        default_average_arrivals_path,
        default_output_dir,
        export_processed,
        format_distance_label,
        hammer_global_time_signal,
        load_annotations,
        load_average_arrivals,
        load_signal,
        save_annotations,
        save_average_arrivals,
    )

try:
    from .masw_dispersion import (
        auto_extract_dispersion_curve,
        common_finite_window,
        phase_shift_dispersion_image,
    )
    from .masw_inversion import monte_carlo_inversion
except ImportError:  # pragma: no cover - script execution from this folder
    from masw_dispersion import (
        auto_extract_dispersion_curve,
        common_finite_window,
        phase_shift_dispersion_image,
    )
    from masw_inversion import monte_carlo_inversion


def _plot_finite_segments(plot_widget, x: np.ndarray, y: np.ndarray, pen) -> None:
    """Dibuja `y` en tramos finitos separados, sin pasarle nunca NaN a
    pyqtgraph.

    `connect="finite"` deberia bastar para esto, pero con arrays largos que
    tienen muchos NaN (curvas de distinta duracion, como en el waterfall)
    dispara un access violation nativo en QPainter.drawPath en esta version
    de pyqtgraph/Qt (no es un problema de los datos). Partir el trazo en
    segmentos finitos y llamar plot() una vez por segmento evita el crash y
    se ve exactamente igual (huecos donde no hay dato real)."""
    finite = np.isfinite(y)
    if not np.any(finite):
        return
    idx = np.flatnonzero(finite)
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks + 1, [idx.size]))
    for s, e in zip(starts, ends):
        if e - s < 2:
            continue
        i0, i1 = idx[s], idx[e - 1] + 1
        plot_widget.plot(x[i0:i1], y[i0:i1], pen=pen)


class FieldReviewWindow(QMainWindow):
    def __init__(
        self,
        dataset: FieldDataset,
        annotations_path: str | Path,
        output_dir: str | Path | None = None,
        prefer_filtered: bool = False,
    ) -> None:
        super().__init__()
        self.dataset = dataset
        self.annotations_path = Path(annotations_path)
        self.output_dir = Path(output_dir) if output_dir else default_output_dir(dataset.raw_root)
        self.prefer_filtered = prefer_filtered
        self.annotations = load_annotations(self.annotations_path)
        self._signal_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._loading = False
        self._current_row = -1
        self._trigger_shortcuts: list[QShortcut] = []
        self.auto_search_window_s: tuple[float, float] | None = None
        self._marking_auto_zone = False
        self._auto_zone_clicks: list[float] = []
        self.auto_zone_lines: list[pg.InfiniteLine] = []
        self.dark_mode = False
        self.trigger_line: pg.InfiniteLine | None = None
        self.geo_trigger_line: pg.InfiniteLine | None = None

        for shot in self.dataset.shots:
            if shot.shot_id not in self.annotations:
                self.annotations[shot.shot_id] = self._safe_auto_pick(shot)

        self.setWindowTitle("Revision Canchita - hammer/geofono")
        self.resize(1380, 860)
        self._build_ui()
        self._populate_table()
        self._apply_filter(select=False)
        visible = self._visible_rows()
        if visible:
            self.table.selectRow(visible[0])
            self._select_row(visible[0])

    def _build_ui(self) -> None:
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        root = QSplitter(Qt.Orientation.Horizontal)
        self.review_root = root

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)

        summary = (
            f"{len(self.dataset.shots)} capturas | "
            f"{self.dataset.duplicate_folder_count} carpetas duplicadas ignoradas"
        )
        self.summary_label = QLabel(summary)
        left_layout.addWidget(self.summary_label)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Usar", "Rev", "Dist", "Trigger s", "Carpeta", "Captura", "Hash"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.table.currentCellChanged.connect(lambda row, *_: self._select_row(row))
        left_layout.addWidget(self.table, stretch=1)

        filter_box = QHBoxLayout()
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["Todas", "Sin revision", "Marcadas con N metros"])
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        self.filter_distance_spin = QDoubleSpinBox()
        self.filter_distance_spin.setRange(-9999.0, 9999.0)
        self.filter_distance_spin.setDecimals(3)
        self.filter_distance_spin.setSingleStep(1.0)
        self.filter_distance_spin.valueChanged.connect(self._apply_filter)
        self.filter_current_btn = QPushButton("N = actual")
        self.filter_current_btn.clicked.connect(self._filter_to_current_distance)
        self.show_same_label_btn = QPushButton("Mostrar mismo label")
        self.show_same_label_btn.clicked.connect(self._show_same_label)
        filter_box.addWidget(QLabel("Filtro"))
        filter_box.addWidget(self.filter_combo, stretch=1)
        filter_box.addWidget(self.filter_distance_spin)
        filter_box.addWidget(self.filter_current_btn)
        filter_box.addWidget(self.show_same_label_btn)
        left_layout.addLayout(filter_box)

        controls = QGroupBox("Marca actual")
        form = QFormLayout(controls)
        self.position_label = QLabel("-")
        self.trigger_label = QLabel("-")
        self.distance_spin = QDoubleSpinBox()
        self.distance_spin.setRange(-9999.0, 9999.0)
        self.distance_spin.setDecimals(3)
        self.distance_spin.setSingleStep(1.0)
        self.distance_spin.valueChanged.connect(self._distance_changed)
        self.accept_check = QCheckBox("Usar esta muestra")
        self.accept_check.toggled.connect(self._accepted_changed)
        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("nota opcional")
        self.notes_edit.editingFinished.connect(self._notes_changed)
        form.addRow("Posicion", self.position_label)
        form.addRow("Distancia m", self.distance_spin)
        form.addRow("", self.accept_check)
        form.addRow("Trigger hammer", self.trigger_label)
        form.addRow("Notas", self.notes_edit)
        left_layout.addWidget(controls)
        shortcut_hint = QLabel(
            "Trigger: arrastrar linea naranja o usar ←/→. "
            "Auto: opcionalmente marcar zona con dos clicks y aplicar a la señal actual o visibles."
        )
        shortcut_hint.setWordWrap(True)
        left_layout.addWidget(shortcut_hint)

        nav = QGridLayout()
        self.prev_btn = QPushButton("Anterior")
        self.prev_btn.clicked.connect(lambda: self._move_row(-1))
        self.next_btn = QPushButton("Siguiente")
        self.next_btn.clicked.connect(lambda: self._move_row(1))
        self.save_next_btn = QPushButton("Guardar y siguiente")
        self.save_next_btn.clicked.connect(self._save_and_next)
        self.auto_btn = QPushButton("Auto")
        self.auto_btn.clicked.connect(self._reset_auto)
        self.auto_visible_btn = QPushButton("Auto visibles")
        self.auto_visible_btn.clicked.connect(self._auto_visible)
        self.auto_zone_btn = QPushButton("Marcar zona auto")
        self.auto_zone_btn.clicked.connect(self._start_auto_zone_marking)
        self.clear_auto_zone_btn = QPushButton("Limpiar zona")
        self.clear_auto_zone_btn.clicked.connect(self._clear_auto_zone)
        self.apply_folder_btn = QPushButton("Aplicar dist. a carpeta")
        self.apply_folder_btn.clicked.connect(self._apply_distance_to_folder)
        self.save_btn = QPushButton("Guardar marcas")
        self.save_btn.clicked.connect(self._save)
        self.export_btn = QPushButton("Exportar")
        self.export_btn.clicked.connect(self._export)
        self.avg_review_btn = QPushButton("Ir a promedios / arrivals")
        self.avg_review_btn.clicked.connect(self._open_average_review)
        nav.addWidget(self.prev_btn, 0, 0)
        nav.addWidget(self.next_btn, 0, 1)
        nav.addWidget(self.auto_btn, 1, 0)
        nav.addWidget(self.auto_visible_btn, 1, 1)
        nav.addWidget(self.auto_zone_btn, 2, 0)
        nav.addWidget(self.clear_auto_zone_btn, 2, 1)
        nav.addWidget(self.apply_folder_btn, 3, 0, 1, 2)
        nav.addWidget(self.save_btn, 4, 0)
        nav.addWidget(self.save_next_btn, 4, 1)
        nav.addWidget(self.export_btn, 5, 0, 1, 2)
        nav.addWidget(self.avg_review_btn, 6, 0, 1, 2)
        left_layout.addLayout(nav)

        overlay_box = QHBoxLayout()
        self.overlay_check = QCheckBox("Mostrar señales con mismo label")
        self.overlay_check.setChecked(True)
        self.overlay_check.toggled.connect(self._refresh_plot)
        self.overlay_count = QSpinBox()
        self.overlay_count.setRange(1, 50)
        self.overlay_count.setValue(12)
        self.overlay_count.valueChanged.connect(self._refresh_plot)
        overlay_box.addWidget(self.overlay_check)
        overlay_box.addWidget(QLabel("max"))
        overlay_box.addWidget(self.overlay_count)
        left_layout.addLayout(overlay_box)

        self.theme_btn = QPushButton("Modo oscuro")
        self.theme_btn.clicked.connect(self._toggle_theme)
        left_layout.addWidget(self.theme_btn)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 4, 8, 8)
        self.plot_widget = pg.GraphicsLayoutWidget()
        suffix = "raw" if not self.prefer_filtered else "filtrada"
        self.hammer_plot = self.plot_widget.addPlot(row=0, col=0, title=f"Hammer {suffix} (DC removido)")
        self.geo_plot = self.plot_widget.addPlot(row=1, col=0, title=f"Geofono {suffix} alineado al hammer (DC removido)")
        self.hammer_plot.showGrid(x=True, y=True, alpha=0.25)
        self.geo_plot.showGrid(x=True, y=True, alpha=0.25)
        self.hammer_plot.scene().sigMouseClicked.connect(self._hammer_plot_clicked)
        self.geo_plot.setLabel("bottom", "Tiempo relativo al hammer", units="s")
        self.hammer_plot.setLabel("left", "Hammer", units="V")
        self.geo_plot.setLabel("left", "Geo", units="V")
        right_layout.addWidget(self.plot_widget, stretch=1)
        self.status_label = QLabel("Listo")
        right_layout.addWidget(self.status_label)

        root.addWidget(left)
        root.addWidget(right)
        root.setSizes([480, 900])
        self.tabs.addTab(root, "Capturas")

        self.masw_panel = MaswPanel(dark_mode=self.dark_mode)
        self.waterfall_panel = WaterfallPanel(
            dark_mode=self.dark_mode,
            on_show_masw=self._show_masw_tab,
            on_auto_masw=self._auto_masw_tab,
        )
        self.average_panel = AverageReviewPanel(
            dataset=self.dataset,
            annotations=self.annotations,
            output_dir=self.output_dir,
            prefer_filtered=self.prefer_filtered,
            dark_mode=self.dark_mode,
            on_show_waterfall=self._show_waterfall_tab,
        )
        self.tabs.addTab(self.average_panel, "Promedios / arrivals")
        self.tabs.addTab(self.waterfall_panel, "Waterfall")
        self.tabs.addTab(self.masw_panel, "MASW")
        self.tabs.currentChanged.connect(self._tab_changed)

        self._install_trigger_shortcuts()
        self._apply_theme()

    def _tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is self.average_panel:
            self._save()
            self.average_panel.set_output_dir(self.output_dir)
            self.average_panel.refresh()

    def _show_waterfall_tab(
        self, common_time, distances, matrix, arrivals, hammer_global, n_averages
    ) -> None:
        self.waterfall_panel.populate(common_time, distances, matrix, arrivals, hammer_global, n_averages)
        self.tabs.setCurrentWidget(self.waterfall_panel)

    def _show_masw_tab(self, common_time, distances, matrix) -> None:
        self.masw_panel.set_data(common_time, distances, matrix)
        self.tabs.setCurrentWidget(self.masw_panel)

    def _auto_masw_tab(self, common_time, distances, matrix) -> None:
        self.tabs.setCurrentWidget(self.masw_panel)
        QApplication.processEvents()
        self.masw_panel.run_auto(common_time, distances, matrix)

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self.dataset.shots))
        for row, shot in enumerate(self.dataset.shots):
            if row < self.table.rowCount():
                self._update_table_row(row, shot)

    def _update_table_row(self, row: int, shot: FieldShot) -> None:
        ann = self.annotations.get(shot.shot_id)
        accepted = ann.accepted if ann else True
        dist = ann.distance_m if ann else shot.distance_m
        trigger_s = ann.trigger_s if ann else 0.0
        reviewed = bool(ann.reviewed) if ann else False
        values = [
            "OK" if accepted else "NO",
            "SI" if reviewed else "",
            f"{dist:.3f}",
            f"{trigger_s:.4f}",
            shot.folder_name,
            shot.capture_name,
            shot.folder_hash[:8],
        ]
        for col, value in enumerate(values):
            item = self.table.item(row, col)
            if item is None:
                item = QTableWidgetItem()
                self.table.setItem(row, col, item)
            item.setText(value)
            if not accepted:
                item.setForeground(QColor("#8a8f98" if self.dark_mode else "#777777"))
            else:
                item.setForeground(QColor("#eeeeee" if self.dark_mode else "#000000"))
            item.setBackground(QColor("#24272e" if self.dark_mode else "#ffffff"))

    def _apply_filter(self, *_args, select: bool = True, keep_current: bool = True) -> None:
        visible: list[int] = []
        for row, shot in enumerate(self.dataset.shots):
            match = self._row_matches_filter(shot)
            self.table.setRowHidden(row, not match)
            if match:
                visible.append(row)
        self._update_summary(len(visible))
        if not select or not visible:
            return
        current_visible = self._current_row in visible
        if keep_current and current_visible:
            target = self._current_row
        else:
            target = visible[0]
        self.table.selectRow(target)
        self._select_row(target)

    def _row_matches_filter(self, shot: FieldShot) -> bool:
        ann = self._annotation(shot)
        mode = self.filter_combo.currentText()
        if mode == "Sin revision":
            return not ann.reviewed
        if mode == "Marcadas con N metros":
            return abs(float(ann.distance_m) - float(self.filter_distance_spin.value())) <= 0.0005
        return True

    def _visible_rows(self) -> list[int]:
        return [row for row in range(len(self.dataset.shots)) if not self.table.isRowHidden(row)]

    def _update_summary(self, visible_count: int | None = None) -> None:
        if visible_count is None:
            visible_count = len(self._visible_rows())
        signal_kind = "cruda" if not self.prefer_filtered else "filtrada"
        self.summary_label.setText(
            f"{visible_count}/{len(self.dataset.shots)} visibles | "
            f"{self.dataset.duplicate_folder_count} carpetas duplicadas ignoradas | "
            f"senal {signal_kind}"
        )

    def _filter_to_current_distance(self) -> None:
        current = self._current()
        if current is None:
            return
        _shot, ann = current
        self.filter_distance_spin.setValue(float(ann.distance_m))
        self.filter_combo.setCurrentText("Marcadas con N metros")
        self._apply_filter()

    def _show_same_label(self) -> None:
        current = self._current()
        if current is None:
            return
        _shot, ann = current
        self.overlay_check.setChecked(True)
        self.filter_distance_spin.setValue(float(ann.distance_m))
        self.filter_combo.setCurrentText("Marcadas con N metros")
        self._apply_filter(keep_current=True)
        self.status_label.setText(
            f"Mostrando señales con label {format_distance_label(ann.distance_m)}"
        )

    def _select_row(self, row: int) -> None:
        if self._loading or row < 0 or row >= len(self.dataset.shots):
            return
        self._current_row = row
        shot = self.dataset.shots[row]
        ann = self._annotation(shot)
        self._loading = True
        self.position_label.setText(f"{row + 1} / {len(self.dataset.shots)}")
        self.distance_spin.setValue(float(ann.distance_m))
        self.accept_check.setChecked(bool(ann.accepted))
        self.notes_edit.setText(ann.notes)
        self._loading = False
        self._refresh_plot()
        self._update_labels()

    def _annotation(self, shot: FieldShot) -> PickAnnotation:
        ann = self.annotations.get(shot.shot_id)
        if ann is None:
            ann = self._safe_auto_pick(shot)
            self.annotations[shot.shot_id] = ann
        return ann

    def _current(self) -> tuple[FieldShot, PickAnnotation] | None:
        if self._current_row < 0 or self._current_row >= len(self.dataset.shots):
            return None
        shot = self.dataset.shots[self._current_row]
        return shot, self._annotation(shot)

    def _safe_auto_pick(self, shot: FieldShot) -> PickAnnotation:
        try:
            return auto_pick_shot(
                shot,
                prefer_filtered=self.prefer_filtered,
                search_window_s=self.auto_search_window_s,
            )
        except Exception:
            return PickAnnotation(
                shot_id=shot.shot_id,
                trigger_s=0.0,
                arrival_s=0.0,
                distance_m=float(shot.distance_m),
                accepted=True,
                source="auto_failed",
            )

    def _load_pair(self, shot: FieldShot) -> tuple[np.ndarray, np.ndarray]:
        cached = self._signal_cache.get(shot.shot_id)
        if cached is not None:
            return cached
        hammer = load_signal(shot.hammer, prefer_filtered=self.prefer_filtered, apply_invert=True)
        geo = load_signal(shot.geo, prefer_filtered=self.prefer_filtered, apply_invert=True)
        n = min(hammer.size, geo.size)
        hammer = hammer[:n]
        geo = geo[:n]
        self._signal_cache[shot.shot_id] = (hammer, geo)
        return hammer, geo

    def _zeroed_pair(self, shot: FieldShot, ann: PickAnnotation) -> tuple[np.ndarray, np.ndarray]:
        hammer, geo = self._load_pair(shot)
        fs = shot.fs or shot.geo.fs or shot.hammer.fs
        trigger_idx = int(np.clip(round(ann.trigger_s * fs), 0, max(0, hammer.size - 1)))
        return self._zero_by_pretrigger(hammer, trigger_idx, fs), self._zero_by_pretrigger(geo, trigger_idx, fs)

    def _refresh_plot(self) -> None:
        current = self._current()
        if current is None:
            return
        shot, ann = current
        fs = shot.fs or shot.geo.fs or shot.hammer.fs
        hammer, geo = self._zeroed_pair(shot, ann)
        n = min(hammer.size, geo.size)
        if n == 0 or fs <= 0:
            return
        time = np.arange(n, dtype=np.float64) / fs
        geo_time = time - float(ann.trigger_s)

        self.hammer_plot.clear()
        self.geo_plot.clear()
        self._style_plots()
        self._plot_overlays(shot, ann)
        hammer_color, geo_color, _overlay_color = self._plot_colors()
        self.hammer_plot.plot(time, hammer, pen=pg.mkPen(hammer_color, width=2.0), name="hammer")
        self.geo_plot.plot(geo_time, geo, pen=pg.mkPen(geo_color, width=2.0), name="geo")

        self.trigger_line = pg.InfiniteLine(
            pos=ann.trigger_s,
            angle=90,
            movable=True,
            bounds=(0.0, float(time[-1])),
            pen=pg.mkPen("#ff9f43" if self.dark_mode else "#e67e22", width=4),
            hoverPen=pg.mkPen("#ffe0a3" if self.dark_mode else "#ff9f43", width=6),
            label="hammer {value:.4f}s",
            labelOpts={"position": 0.92},
        )
        self.trigger_line.sigPositionChanged.connect(self._trigger_line_changed)
        self.trigger_line.sigPositionChangeFinished.connect(self._trigger_line_change_finished)
        self.trigger_line.setZValue(20)
        self.hammer_plot.addItem(self.trigger_line)
        self.geo_trigger_line = pg.InfiniteLine(
            pos=0.0,
            angle=90,
            movable=False,
            pen=pg.mkPen("#ff9f43" if self.dark_mode else "#e67e22", style=Qt.PenStyle.DashLine),
        )
        self.geo_plot.addItem(self.geo_trigger_line)
        self._draw_auto_search_window()
        self.hammer_plot.setXRange(max(0.0, ann.trigger_s - 0.15), min(float(time[-1]), ann.trigger_s + 0.65), padding=0.02)
        self.geo_plot.setXRange(max(float(geo_time[0]), -0.08), min(float(geo_time[-1]), 1.1), padding=0.02)
        self.status_label.setText(f"{shot.folder_name} / {shot.capture_name}")

    def _draw_auto_search_window(self) -> None:
        self.auto_zone_lines = []
        if self.auto_search_window_s is None:
            return
        a, b = sorted(self.auto_search_window_s)
        for value in (a, b):
            line = pg.InfiniteLine(
                pos=value,
                angle=90,
                movable=False,
                pen=pg.mkPen("#3da5ff" if self.dark_mode else "#1f77b4", width=2, style=Qt.PenStyle.DashLine),
            )
            line.setZValue(15)
            self.hammer_plot.addItem(line)
            self.auto_zone_lines.append(line)

    def _hammer_plot_clicked(self, event) -> None:
        if not self._marking_auto_zone:
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self.hammer_plot.sceneBoundingRect().contains(event.scenePos()):
            return
        point = self.hammer_plot.vb.mapSceneToView(event.scenePos())
        x = float(point.x())
        current = self._current()
        if current is not None:
            shot, _ann = current
            hammer, _geo = self._load_pair(shot)
            fs = shot.fs or shot.geo.fs or shot.hammer.fs
            if fs > 0 and hammer.size:
                x = float(np.clip(x, 0.0, (hammer.size - 1) / fs))
        self._auto_zone_clicks.append(x)
        if len(self._auto_zone_clicks) == 1:
            self.status_label.setText(f"Zona auto: primer punto {x:.5f}s, falta segundo click")
        else:
            a, b = sorted(self._auto_zone_clicks[:2])
            self.auto_search_window_s = (a, b)
            self._marking_auto_zone = False
            self._auto_zone_clicks = []
            self.status_label.setText(f"Zona auto definida: {a:.5f}s a {b:.5f}s")
            self._refresh_plot()
        event.accept()

    def _plot_overlays(self, current_shot: FieldShot, current_ann: PickAnnotation) -> None:
        if not self.overlay_check.isChecked():
            return
        target = round(float(current_ann.distance_m), 3)
        count = 0
        max_count = int(self.overlay_count.value())
        for shot in self.dataset.shots:
            if shot.shot_id == current_shot.shot_id:
                continue
            ann = self.annotations.get(shot.shot_id)
            if ann is None or not ann.accepted:
                continue
            if round(float(ann.distance_m), 3) != target:
                continue
            fs = shot.fs or shot.geo.fs or shot.hammer.fs
            if fs <= 0:
                continue
            hammer, geo = self._zeroed_pair(shot, ann)
            n = min(hammer.size, geo.size)
            if n == 0:
                continue
            time = np.arange(n, dtype=np.float64) / fs - float(ann.trigger_s)
            _hammer_color, _geo_color, overlay_color = self._plot_colors()
            pen = pg.mkPen(overlay_color, width=1)
            self.geo_plot.plot(time, geo, pen=pen)
            count += 1
            if count >= max_count:
                break

    def _trigger_line_changed(self) -> None:
        if self._loading:
            return
        current = self._current()
        if current is None or self.trigger_line is None:
            return
        shot, ann = current
        ann.trigger_s = max(0.0, float(self.trigger_line.value()))
        ann.arrival_s = ann.trigger_s
        ann.source = "manual"
        if self.geo_trigger_line is not None:
            self.geo_trigger_line.setValue(0.0)
        self._update_table_row(self._current_row, shot)
        self._update_labels()

    def _trigger_line_change_finished(self) -> None:
        self._trigger_line_changed()
        self._refresh_plot()

    def _nudge_trigger(self, direction: int, step_s: float) -> None:
        current = self._current()
        if current is None:
            return
        shot, ann = current
        hammer, _geo = self._load_pair(shot)
        fs = shot.fs or shot.geo.fs or shot.hammer.fs
        if fs <= 0 or hammer.size == 0:
            return
        max_s = (hammer.size - 1) / fs
        ann.trigger_s = float(np.clip(ann.trigger_s + direction * step_s, 0.0, max_s))
        ann.arrival_s = ann.trigger_s
        ann.source = "manual"
        if self.trigger_line is not None:
            self.trigger_line.blockSignals(True)
            self.trigger_line.setValue(ann.trigger_s)
            self.trigger_line.blockSignals(False)
        self._update_table_row(self._current_row, shot)
        self._update_labels()
        self._refresh_plot()

    def _nudge_trigger_by_samples(self, direction: int, samples: int) -> None:
        current = self._current()
        if current is None:
            return
        shot, _ann = current
        fs = shot.fs or shot.geo.fs or shot.hammer.fs
        if fs <= 0:
            return
        self._nudge_trigger(direction, float(samples) / fs)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        key = event.key()
        if key not in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            super().keyPressEvent(event)
            return
        if self.tabs.currentWidget() is not self.review_root:
            super().keyPressEvent(event)
            return
        current = self._current()
        if current is None:
            super().keyPressEvent(event)
            return
        shot, _ann = current
        fs = shot.fs or shot.geo.fs or shot.hammer.fs
        if fs <= 0:
            super().keyPressEvent(event)
            return
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            step_s = 0.001
        else:
            samples = 10 if modifiers & Qt.KeyboardModifier.ShiftModifier else 1
            step_s = samples / fs
        direction = -1 if key == Qt.Key.Key_Left else 1
        self._nudge_trigger(direction, step_s)
        event.accept()

    def _distance_changed(self, value: float) -> None:
        if self._loading:
            return
        current = self._current()
        if current is None:
            return
        shot, ann = current
        ann.distance_m = float(value)
        ann.source = "manual"
        self._update_table_row(self._current_row, shot)
        self._refresh_plot()

    def _accepted_changed(self, checked: bool) -> None:
        if self._loading:
            return
        current = self._current()
        if current is None:
            return
        shot, ann = current
        ann.accepted = bool(checked)
        ann.source = "manual"
        self._update_table_row(self._current_row, shot)

    def _notes_changed(self) -> None:
        current = self._current()
        if current is None:
            return
        _shot, ann = current
        ann.notes = self.notes_edit.text()
        ann.source = "manual"

    def _toggle_theme(self) -> None:
        self.dark_mode = not self.dark_mode
        self._apply_theme()
        self._refresh_plot()

    def _apply_theme(self) -> None:
        if self.dark_mode:
            self.setStyleSheet(
                """
                QMainWindow { background: #1f2329; color: #eeeeee; }
                QWidget { background: #1f2329; color: #eeeeee; }
                QTableWidget { background: #161a20; color: #eeeeee; gridline-color: #333944; }
                QTableWidget::item { background: #24272e; color: #eeeeee; }
                QTableWidget::item:selected { background: #3b4a61; color: #ffffff; }
                QHeaderView::section { background: #2a3038; color: #eeeeee; border: 1px solid #3b4350; }
                QTableCornerButton::section { background: #2a3038; border: 1px solid #3b4350; }
                QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {
                    background: #14181d; color: #eeeeee; border: 1px solid #3b4350; padding: 2px;
                }
                QComboBox QAbstractItemView {
                    background: #14181d; color: #eeeeee; selection-background-color: #3b4a61;
                }
                QPushButton { background: #2f3742; color: #eeeeee; border: 1px solid #4b5563; padding: 5px; }
                QPushButton:hover { background: #3b4552; }
                QCheckBox { color: #eeeeee; }
                QGroupBox { border: 1px solid #3b4350; margin-top: 8px; }
                QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; }
                QTabWidget::pane { border: 1px solid #3b4350; }
                QTabBar::tab { background: #2a3038; color: #eeeeee; border: 1px solid #3b4350; padding: 6px 14px; }
                QTabBar::tab:selected { background: #3b4a61; }
                """
            )
            self.theme_btn.setText("Modo claro")
        else:
            self.setStyleSheet(
                """
                QMainWindow { background: #f5f6f8; color: #111111; }
                QWidget { background: #f5f6f8; color: #111111; }
                QLabel { background: transparent; color: #111111; }
                QTableWidget { background: #ffffff; color: #111111; gridline-color: #d7dbe2; }
                QTableWidget::item { background: #ffffff; color: #111111; }
                QTableWidget::item:selected { background: #d9e8ff; color: #000000; }
                QHeaderView::section { background: #e9edf3; color: #111111; border: 1px solid #cbd2dc; }
                QTableCornerButton::section { background: #e9edf3; border: 1px solid #cbd2dc; }
                QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {
                    background: #ffffff; color: #111111; border: 1px solid #b9c0ca; padding: 2px;
                }
                QComboBox QAbstractItemView {
                    background: #ffffff; color: #111111; selection-background-color: #d9e8ff;
                }
                QPushButton { background: #f0f2f5; color: #111111; border: 1px solid #b9c0ca; padding: 5px; }
                QPushButton:hover { background: #e2e8f0; }
                QCheckBox { color: #111111; }
                QGroupBox { border: 1px solid #cbd2dc; margin-top: 8px; color: #111111; }
                QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; }
                QTabWidget::pane { border: 1px solid #cbd2dc; }
                QTabBar::tab { background: #e9edf3; color: #111111; border: 1px solid #cbd2dc; padding: 6px 14px; }
                QTabBar::tab:selected { background: #ffffff; }
                """
            )
            self.theme_btn.setText("Modo oscuro")
        self._style_plots()
        for row, shot in enumerate(self.dataset.shots):
            self._update_table_row(row, shot)
        if hasattr(self, "average_panel"):
            self.average_panel.set_dark_mode(self.dark_mode)
        if hasattr(self, "waterfall_panel"):
            self.waterfall_panel.set_dark_mode(self.dark_mode)
        if hasattr(self, "masw_panel"):
            self.masw_panel.set_dark_mode(self.dark_mode)

    def _style_plots(self) -> None:
        bg = "#15181d" if self.dark_mode else "#ffffff"
        fg = "#eeeeee" if self.dark_mode else "#222222"
        grid_alpha = 0.18 if self.dark_mode else 0.25
        self.plot_widget.setBackground(bg)
        suffix = "raw" if not self.prefer_filtered else "filtrada"
        for plot, title in (
            (self.hammer_plot, f"Hammer {suffix} (DC removido)"),
            (self.geo_plot, f"Geofono {suffix} alineado al hammer (DC removido)"),
        ):
            plot.setTitle(title, color=fg)
            plot.showGrid(x=True, y=True, alpha=grid_alpha)
            for axis_name in ("bottom", "left"):
                axis = plot.getAxis(axis_name)
                axis.setPen(pg.mkPen(fg))
                axis.setTextPen(pg.mkPen(fg))

    def _plot_colors(self) -> tuple[str, str, tuple[int, int, int, int]]:
        if self.dark_mode:
            return "#ffb86b", "#69b7ff", (210, 210, 210, 85)
        return "#cc5a00", "#0066cc", (120, 120, 120, 80)

    def _install_trigger_shortcuts(self) -> None:
        shortcuts = [
            ("Left", lambda: self._nudge_trigger_by_samples(-1, 1)),
            ("Right", lambda: self._nudge_trigger_by_samples(1, 1)),
            ("Shift+Left", lambda: self._nudge_trigger_by_samples(-1, 10)),
            ("Shift+Right", lambda: self._nudge_trigger_by_samples(1, 10)),
            ("Ctrl+Left", lambda: self._nudge_trigger(-1, 0.001)),
            ("Ctrl+Right", lambda: self._nudge_trigger(1, 0.001)),
        ]
        for sequence, callback in shortcuts:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(lambda cb=callback: self._run_trigger_shortcut(cb))
            self._trigger_shortcuts.append(shortcut)

    def _run_trigger_shortcut(self, callback) -> None:
        if self.tabs.currentWidget() is not self.review_root:
            return
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox)):
            return
        callback()

    def _update_labels(self) -> None:
        current = self._current()
        if current is None:
            return
        _shot, ann = current
        self.trigger_label.setText(f"{ann.trigger_s:.6f} s")

    def _move_row(self, delta: int) -> None:
        if not self.dataset.shots:
            return
        row = self._visible_row_near(self._current_row, delta)
        if row is None:
            return
        self.table.selectRow(row)
        self._select_row(row)

    def _visible_row_near(self, row: int, delta: int) -> int | None:
        visible = self._visible_rows()
        if not visible:
            return None
        if row not in visible:
            if delta >= 0:
                for candidate in visible:
                    if candidate > row:
                        return candidate
                return visible[-1]
            for candidate in reversed(visible):
                if candidate < row:
                    return candidate
            return visible[0]
        pos = visible.index(row)
        next_pos = int(np.clip(pos + delta, 0, len(visible) - 1))
        return visible[next_pos]

    def _reset_auto(self) -> None:
        current = self._current()
        if current is None:
            return
        shot, _ann = current
        old = self.annotations.get(shot.shot_id)
        ann = self._safe_auto_pick(shot)
        if old is not None:
            ann.distance_m = old.distance_m
            ann.accepted = old.accepted
            ann.notes = old.notes
        ann.reviewed = False
        self.annotations[shot.shot_id] = ann
        self._select_row(self._current_row)
        self._update_table_row(self._current_row, shot)
        self._apply_filter()

    def _auto_visible(self) -> None:
        rows = self._visible_rows()
        if not rows:
            return
        for row in rows:
            shot = self.dataset.shots[row]
            old = self.annotations.get(shot.shot_id)
            ann = self._safe_auto_pick(shot)
            if old is not None:
                ann.distance_m = old.distance_m
                ann.accepted = old.accepted
                ann.notes = old.notes
            ann.reviewed = False
            self.annotations[shot.shot_id] = ann
            self._update_table_row(row, shot)
        self.status_label.setText(f"Auto aplicado a {len(rows)} señales visibles")
        self._select_row(self._current_row if self._current_row in rows else rows[0])
        self._apply_filter(keep_current=True)

    def _start_auto_zone_marking(self) -> None:
        self._marking_auto_zone = True
        self._auto_zone_clicks = []
        self.status_label.setText("Zona auto: hacé dos clicks sobre el hammer para marcar inicio y fin")

    def _clear_auto_zone(self) -> None:
        self.auto_search_window_s = None
        self._marking_auto_zone = False
        self._auto_zone_clicks = []
        self.status_label.setText("Zona auto limpiada; Auto buscará en toda la señal")
        self._refresh_plot()

    def _apply_distance_to_folder(self) -> None:
        current = self._current()
        if current is None:
            return
        shot, ann = current
        distance = float(ann.distance_m)
        changed = 0
        for row, other in enumerate(self.dataset.shots):
            if other.folder_name != shot.folder_name:
                continue
            other_ann = self._annotation(other)
            other_ann.distance_m = distance
            other_ann.source = "manual"
            self._update_table_row(row, other)
            changed += 1
        self.status_label.setText(f"Distancia {distance:.3f} m aplicada a {changed} capturas")
        self._apply_filter(keep_current=True)
        self._refresh_plot()

    def _save(self) -> None:
        try:
            save_annotations(self.annotations_path, self.dataset, self.annotations)
        except Exception as exc:
            QMessageBox.critical(self, "No se pudo guardar", str(exc))
            return
        self.status_label.setText(f"Marcas guardadas en {self.annotations_path}")

    def _save_and_next(self) -> None:
        current = self._current()
        if current is None:
            return
        shot, ann = current
        old_row = self._current_row
        ann.reviewed = True
        ann.source = "manual"
        self._update_table_row(old_row, shot)
        try:
            save_annotations(self.annotations_path, self.dataset, self.annotations)
        except Exception as exc:
            QMessageBox.critical(self, "No se pudo guardar", str(exc))
            return
        self._apply_filter(select=False)
        next_row = self._visible_row_near(old_row, 1)
        if next_row is None:
            self.status_label.setText("No quedan capturas visibles para este filtro")
            return
        self.table.selectRow(next_row)
        self._select_row(next_row)
        self.status_label.setText(f"Guardada {format_distance_label(ann.distance_m)}; siguiente captura cargada")

    def _export(self) -> None:
        self._save()
        selected = QFileDialog.getExistingDirectory(
            self,
            "Carpeta de salida",
            str(self.output_dir),
        )
        if selected:
            self.output_dir = Path(selected)
            self.average_panel.set_output_dir(self.output_dir)
        self.export_btn.setEnabled(False)
        self.status_label.setText("Exportando todas las muestras y promedios a disco, un momento...")
        QApplication.processEvents()
        try:
            self.average_panel.save_arrivals()
            average_arrivals = load_average_arrivals(default_average_arrivals_path(self.output_dir))
            result = export_processed(
                self.dataset,
                self.annotations,
                self.output_dir,
                prefer_filtered=self.prefer_filtered,
                average_arrivals=average_arrivals,
            )
        except Exception as exc:
            QMessageBox.critical(self, "No se pudo exportar", str(exc))
            return
        finally:
            self.export_btn.setEnabled(True)
        self.status_label.setText(f"Exportacion lista en {result.output_dir}")
        QMessageBox.information(
            self,
            "Exportacion lista",
            (
                f"Muestras: {result.sample_count}\n"
                f"Promedios: {result.average_count}\n"
                f"Saltadas: {result.skipped_count}\n"
                f"Salida: {result.output_dir}"
            ),
        )

    def _open_average_review(self) -> None:
        self.tabs.setCurrentWidget(self.average_panel)

    @staticmethod
    def _zero_by_pretrigger(signal: np.ndarray, trigger_idx: int, fs: float) -> np.ndarray:
        if signal.size == 0:
            return signal
        start = max(0, trigger_idx - int(round(fs * 0.25)))
        end = max(start + 1, trigger_idx - int(round(fs * 0.005)))
        end = min(end, signal.size)
        baseline = float(np.median(signal[start:end])) if end > start else float(np.median(signal))
        return signal.astype(np.float32, copy=False) - np.float32(baseline)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            save_annotations(self.annotations_path, self.dataset, self.annotations)
            self.average_panel.save_arrivals()
        finally:
            super().closeEvent(event)


class AverageReviewPanel(QWidget):
    def __init__(
        self,
        dataset: FieldDataset,
        annotations: dict[str, PickAnnotation],
        output_dir: str | Path,
        prefer_filtered: bool = False,
        dark_mode: bool = False,
        on_show_waterfall=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.dataset = dataset
        self.annotations = annotations
        self.output_dir = Path(output_dir)
        self.prefer_filtered = prefer_filtered
        self.dark_mode = dark_mode
        self.on_show_waterfall = on_show_waterfall
        self.arrivals_path = default_average_arrivals_path(self.output_dir)
        self.arrivals = load_average_arrivals(self.arrivals_path)
        self.averages: list[dict] = []
        self.hammer_global: dict | None = None
        self._current_row = -1
        self._loading = False
        self.arrival_line: pg.InfiniteLine | None = None
        self._computing = False
        self._last_signature: tuple | None = None

        self._build_ui()

    def set_output_dir(self, output_dir: str | Path) -> None:
        output_dir = Path(output_dir)
        if output_dir == self.output_dir:
            return
        self.output_dir = output_dir
        self.arrivals_path = default_average_arrivals_path(self.output_dir)
        self.arrivals = load_average_arrivals(self.arrivals_path)
        self._last_signature = None

    def refresh(self, force: bool = False) -> None:
        self._refresh_averages(force=force)

    def set_dark_mode(self, dark: bool) -> None:
        self.dark_mode = bool(dark)
        self._style_plots()
        for row, avg in enumerate(self.averages):
            self._update_table_row(row, avg)

    def save_arrivals(self) -> None:
        save_average_arrivals(self.arrivals_path, self.arrivals)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        root = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(root)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        self.summary_label = QLabel("Promedios")
        left_layout.addWidget(self.summary_label)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Rev", "Label", "N", "Arrival s"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.currentCellChanged.connect(lambda row, *_: self._select_row(row))
        left_layout.addWidget(self.table, stretch=1)

        form_box = QGroupBox("Arrival del promedio")
        form = QFormLayout(form_box)
        self.label_value = QLabel("-")
        self.arrival_value = QLabel("-")
        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("nota opcional")
        self.notes_edit.editingFinished.connect(self._notes_changed)
        form.addRow("Label", self.label_value)
        form.addRow("Arrival", self.arrival_value)
        form.addRow("Notas", self.notes_edit)
        left_layout.addWidget(form_box)

        nav = QGridLayout()
        self.prev_btn = QPushButton("Anterior")
        self.prev_btn.clicked.connect(lambda: self._move_row(-1))
        self.next_btn = QPushButton("Siguiente")
        self.next_btn.clicked.connect(lambda: self._move_row(1))
        self.save_btn = QPushButton("Guardar arrival")
        self.save_btn.clicked.connect(lambda: self._save_current(move_next=False))
        self.save_next_btn = QPushButton("Guardar y siguiente")
        self.save_next_btn.clicked.connect(lambda: self._save_current(move_next=True))
        self.refresh_btn = QPushButton("Refrescar promedios")
        self.refresh_btn.clicked.connect(lambda: self._refresh_averages(force=True))
        self.preview_btn = QPushButton("Ver waterfall")
        self.preview_btn.clicked.connect(self._show_waterfall)
        self.export_btn = QPushButton("Exportar waterfall")
        self.export_btn.clicked.connect(self._export_waterfall)
        nav.addWidget(self.prev_btn, 0, 0)
        nav.addWidget(self.next_btn, 0, 1)
        nav.addWidget(self.save_btn, 1, 0)
        nav.addWidget(self.save_next_btn, 1, 1)
        nav.addWidget(self.refresh_btn, 2, 0)
        nav.addWidget(self.preview_btn, 2, 1)
        nav.addWidget(self.export_btn, 3, 0, 1, 2)
        left_layout.addLayout(nav)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.plot_widget = pg.GraphicsLayoutWidget()
        self.hammer_plot = self.plot_widget.addPlot(row=0, col=0, title="Hammer promedio")
        self.geo_plot = self.plot_widget.addPlot(row=1, col=0, title="Geofono promedio")
        self.geo_plot.setLabel("bottom", "Tiempo relativo al hammer", units="s")
        self.hammer_plot.setLabel("left", "Hammer", units="V")
        self.geo_plot.setLabel("left", "Geo", units="V")
        right_layout.addWidget(self.plot_widget, stretch=1)
        self.status_label = QLabel("Listo")
        right_layout.addWidget(self.status_label)

        root.addWidget(left)
        root.addWidget(right)
        root.setSizes([360, 820])
        self._style_plots()

    def _refresh_averages(self, force: bool = False) -> None:
        if self._computing:
            return
        signature = annotations_signature(self.dataset, self.annotations)
        if not force and self.averages and signature == self._last_signature:
            self.summary_label.setText(f"{len(self.averages)} promedios (sin cambios) | {self.output_dir}")
            return
        self._computing = True
        self.refresh_btn.setEnabled(False)
        self.summary_label.setText("Calculando promedios...")
        self.status_label.setText("Calculando promedios, un momento...")
        QApplication.processEvents()
        try:
            groups, hammer_global = compute_average_groups(
                self.dataset,
                self.annotations,
                prefer_filtered=self.prefer_filtered,
            )
        except Exception as exc:
            QMessageBox.critical(self, "No se pudieron calcular promedios", str(exc))
            self.summary_label.setText(f"{len(self.averages)} promedios | {self.output_dir}")
            self._computing = False
            self.refresh_btn.setEnabled(True)
            return
        self.averages = groups
        self.hammer_global = hammer_global
        self._last_signature = signature
        for avg in self.averages:
            label = str(avg["label"])
            if label not in self.arrivals:
                self.arrivals[label] = AverageArrivalAnnotation(
                    label=label,
                    distance_m=float(avg["distance_m"]),
                    arrival_s=0.0,
                    reviewed=False,
                )
        self._populate_table()
        self.summary_label.setText(f"{len(self.averages)} promedios | {self.output_dir}")
        if self.averages:
            row = min(max(self._current_row, 0), len(self.averages) - 1)
            self.table.selectRow(row)
            self._select_row(row)
        self._computing = False
        self.refresh_btn.setEnabled(True)

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self.averages))
        for row, avg in enumerate(self.averages):
            self._update_table_row(row, avg)

    def _update_table_row(self, row: int, avg: dict) -> None:
        label = str(avg["label"])
        ann = self.arrivals.get(label)
        values = [
            "SI" if ann and ann.reviewed else "",
            label,
            str(avg.get("n", "")),
            f"{ann.arrival_s:.5f}" if ann else "",
        ]
        for col, value in enumerate(values):
            item = self.table.item(row, col)
            if item is None:
                item = QTableWidgetItem()
                self.table.setItem(row, col, item)
            item.setText(value)
            item.setForeground(QColor("#eeeeee" if self.dark_mode else "#111111"))
            item.setBackground(QColor("#24272e" if self.dark_mode else "#ffffff"))

    def _select_row(self, row: int) -> None:
        if self._loading or row < 0 or row >= len(self.averages):
            return
        self._current_row = row
        avg = self.averages[row]
        label = str(avg["label"])
        ann = self.arrivals[label]
        self._loading = True
        self.label_value.setText(f"{label} ({avg.get('n', '')} señales)")
        self.arrival_value.setText(f"{ann.arrival_s:.6f} s")
        self.notes_edit.setText(ann.notes)
        self._loading = False
        self._refresh_plot(avg, ann)

    def _refresh_plot(self, avg: dict, ann: AverageArrivalAnnotation) -> None:
        time_s = np.asarray(avg["time_s"])
        hammer = np.asarray(avg["hammer_mean_v"])
        geo = np.asarray(avg["geo_mean_v"])
        self.hammer_plot.clear()
        self.geo_plot.clear()
        self._style_plots()
        hammer_color = "#ffb86b" if self.dark_mode else "#cc5a00"
        geo_color = "#69b7ff" if self.dark_mode else "#0066cc"
        _plot_finite_segments(self.hammer_plot, time_s, hammer, pg.mkPen(hammer_color, width=2.0))
        _plot_finite_segments(self.geo_plot, time_s, geo, pg.mkPen(geo_color, width=2.0))
        self.geo_plot.addItem(
            pg.InfiniteLine(
                pos=0.0,
                angle=90,
                movable=False,
                pen=pg.mkPen("#ff9f43" if self.dark_mode else "#e67e22", style=Qt.PenStyle.DashLine),
            )
        )
        self.arrival_line = pg.InfiniteLine(
            pos=float(ann.arrival_s),
            angle=90,
            movable=True,
            pen=pg.mkPen("#61d394" if self.dark_mode else "#2ecc71", width=2),
            label="arrival {value:.4f}s",
            labelOpts={"position": 0.92},
        )
        self.arrival_line.sigPositionChanged.connect(self._arrival_line_changed)
        self.geo_plot.addItem(self.arrival_line)
        x0 = max(float(time_s[0]), min(0.0, ann.arrival_s) - 0.15)
        x1 = min(float(time_s[-1]), max(ann.arrival_s, 0.0) + 0.8)
        if x1 > x0:
            self.hammer_plot.setXRange(x0, x1, padding=0.02)
            self.geo_plot.setXRange(x0, x1, padding=0.02)
        self.status_label.setText(f"Promedio {avg['label']} | n={avg.get('n', '')}")

    def _arrival_line_changed(self) -> None:
        if self._loading or self.arrival_line is None or self._current_row < 0:
            return
        avg = self.averages[self._current_row]
        ann = self.arrivals[str(avg["label"])]
        ann.arrival_s = float(self.arrival_line.value())
        self.arrival_value.setText(f"{ann.arrival_s:.6f} s")
        self._update_table_row(self._current_row, avg)

    def _notes_changed(self) -> None:
        if self._loading or self._current_row < 0:
            return
        avg = self.averages[self._current_row]
        self.arrivals[str(avg["label"])].notes = self.notes_edit.text()

    def _move_row(self, delta: int) -> None:
        if not self.averages:
            return
        row = int(np.clip(self._current_row + delta, 0, len(self.averages) - 1))
        self.table.selectRow(row)
        self._select_row(row)

    def _save_current(self, move_next: bool) -> None:
        if self._current_row < 0:
            return
        avg = self.averages[self._current_row]
        ann = self.arrivals[str(avg["label"])]
        if self.arrival_line is not None:
            ann.arrival_s = float(self.arrival_line.value())
        ann.reviewed = True
        ann.notes = self.notes_edit.text()
        save_average_arrivals(self.arrivals_path, self.arrivals)
        self._update_table_row(self._current_row, avg)
        self.status_label.setText(f"Arrival guardado para {ann.label}")
        if move_next:
            self._move_row(1)

    def _export_waterfall(self) -> None:
        if self._computing:
            return
        save_average_arrivals(self.arrivals_path, self.arrivals)
        self._computing = True
        self.export_btn.setEnabled(False)
        self.status_label.setText("Exportando waterfall a disco, un momento...")
        QApplication.processEvents()
        try:
            result = export_processed(
                self.dataset,
                self.annotations,
                self.output_dir,
                prefer_filtered=self.prefer_filtered,
                average_arrivals=self.arrivals,
            )
        except Exception as exc:
            QMessageBox.critical(self, "No se pudo exportar waterfall", str(exc))
            return
        finally:
            self._computing = False
            self.export_btn.setEnabled(True)
        QMessageBox.information(
            self,
            "Waterfall listo",
            (
                f"Waterfall PNG: {result.waterfall_png}\n"
                f"Waterfall PDF: {result.waterfall_pdf}\n"
                f"Arrivals: {self.arrivals_path}"
            ),
        )
        self.status_label.setText("Waterfall exportado")

    def _show_waterfall(self) -> None:
        if not self.averages:
            self._refresh_averages(force=True)
        if not self.averages:
            QMessageBox.warning(self, "Ver waterfall", "No hay promedios para armar el waterfall")
            return
        try:
            built = build_waterfall_matrix(self.averages)
        except Exception as exc:
            QMessageBox.critical(self, "Ver waterfall", str(exc))
            return
        if built is None:
            QMessageBox.warning(self, "Ver waterfall", "No se pudo armar la base de tiempo comun")
            return
        common_time, distances, matrix = built
        if self.on_show_waterfall is not None:
            self.on_show_waterfall(common_time, distances, matrix, self.arrivals, self.hammer_global, len(self.averages))
        self.status_label.setText(f"Waterfall actualizado con {len(distances)} promedios")

    def _style_plots(self) -> None:
        bg = "#15181d" if self.dark_mode else "#ffffff"
        fg = "#eeeeee" if self.dark_mode else "#222222"
        self.plot_widget.setBackground(bg)
        for plot in (self.hammer_plot, self.geo_plot):
            plot.showGrid(x=True, y=True, alpha=0.18 if self.dark_mode else 0.25)
            for axis_name in ("bottom", "left"):
                axis = plot.getAxis(axis_name)
                axis.setPen(pg.mkPen(fg))
                axis.setTextPen(pg.mkPen(fg))
        self.hammer_plot.setTitle("Hammer promedio", color=fg)
        self.geo_plot.setTitle("Geofono promedio", color=fg)


class WaterfallPanel(QWidget):
    """Tab propio del waterfall: grafico interactivo con cursor (crosshair)
    que muestra tiempo/distancia bajo el mouse. Se llena desde
    AverageReviewPanel via `populate()`, no calcula nada por si mismo."""

    def __init__(
        self,
        dark_mode: bool = False,
        on_show_masw=None,
        on_auto_masw=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.dark_mode = dark_mode
        self.on_show_masw = on_show_masw
        self.on_auto_masw = on_auto_masw
        self._crosshair_v: pg.InfiniteLine | None = None
        self._crosshair_h: pg.InfiniteLine | None = None
        self._proxy = None
        self._last_data: dict | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        top = QHBoxLayout()
        self.info_label = QLabel(
            "Sin datos todavia. Calcula los promedios en la pestaña anterior y presiona 'Ver waterfall'."
        )
        top.addWidget(self.info_label, stretch=1)
        self.raw_amplitude_check = QCheckBox("Amplitud real (ver atenuacion)")
        self.raw_amplitude_check.setToolTip(
            "Por defecto cada traza se normaliza a su propio pico para comparar formas.\n"
            "Con esto activado todas comparten la misma escala, asi se ve como cae la amplitud con la distancia."
        )
        self.raw_amplitude_check.toggled.connect(self._redraw)
        top.addWidget(self.raw_amplitude_check)
        self.masw_btn = QPushButton("Ver MASW")
        self.masw_btn.setToolTip(
            "Analisis MASW paso a paso: imagen de dispersion, picking manual, inversion"
        )
        self.masw_btn.clicked.connect(self._send_to_masw)
        top.addWidget(self.masw_btn)
        self.auto_masw_btn = QPushButton("Auto inversion")
        self.auto_masw_btn.setToolTip(
            "Corre todo el flujo MASW solo: imagen, auto-pick con filtros de calidad e inversion.\n"
            "Los parametros elegidos quedan visibles en la pestaña MASW para poder revisarlos."
        )
        self.auto_masw_btn.clicked.connect(self._send_to_auto_masw)
        top.addWidget(self.auto_masw_btn)
        layout.addLayout(top)
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Tiempo relativo al hammer", units="s")
        self.plot.setLabel("left", "Distancia [m] + amplitud normalizada")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        layout.addWidget(self.plot, stretch=1)
        self.coord_label = QLabel("Move el mouse sobre el grafico para ver tiempo/distancia bajo el cursor")
        layout.addWidget(self.coord_label)

        self._crosshair_v = pg.InfiniteLine(angle=90, movable=False)
        self._crosshair_h = pg.InfiniteLine(angle=0, movable=False)
        self._crosshair_v.setZValue(100)
        self._crosshair_h.setZValue(100)
        self.plot.addItem(self._crosshair_v, ignoreBounds=True)
        self.plot.addItem(self._crosshair_h, ignoreBounds=True)
        self._crosshair_v.hide()
        self._crosshair_h.hide()
        self._proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved, rateLimit=60, slot=self._on_mouse_moved)
        self._apply_theme()

    def _on_mouse_moved(self, evt) -> None:
        pos = evt[0]
        plot_item = self.plot.getPlotItem()
        if not plot_item.sceneBoundingRect().contains(pos):
            self._crosshair_v.hide()
            self._crosshair_h.hide()
            return
        point = plot_item.vb.mapSceneToView(pos)
        x, y = float(point.x()), float(point.y())
        self._crosshair_v.setPos(x)
        self._crosshair_h.setPos(y)
        self._crosshair_v.show()
        self._crosshair_h.show()
        self.coord_label.setText(f"t = {x:.5f} s   |   distancia+amplitud = {y:.3f}")

    def set_dark_mode(self, dark: bool) -> None:
        self.dark_mode = bool(dark)
        self._apply_theme()

    def _apply_theme(self) -> None:
        bg = "#15181d" if self.dark_mode else "#ffffff"
        fg = "#eeeeee" if self.dark_mode else "#222222"
        self.plot.setBackground(bg)
        for axis_name in ("bottom", "left"):
            axis = self.plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(fg))
            axis.setTextPen(pg.mkPen(fg))
        pen = pg.mkPen("#888888" if self.dark_mode else "#aaaaaa", style=Qt.PenStyle.DashLine)
        if self._crosshair_v is not None:
            self._crosshair_v.setPen(pen)
            self._crosshair_h.setPen(pen)

    def populate(
        self,
        common_time: np.ndarray,
        distances: list[float],
        matrix: np.ndarray,
        arrivals: dict[str, AverageArrivalAnnotation] | None,
        hammer_global: dict | None,
        n_averages: int,
    ) -> None:
        self._last_data = {
            "common_time": common_time,
            "distances": distances,
            "matrix": matrix,
            "arrivals": arrivals,
            "hammer_global": hammer_global,
            "n_averages": n_averages,
        }
        self._redraw()

    def _redraw(self) -> None:
        if self._last_data is None:
            return
        common_time = self._last_data["common_time"]
        distances = self._last_data["distances"]
        matrix = self._last_data["matrix"]
        arrivals = self._last_data["arrivals"]
        hammer_global = self._last_data["hammer_global"]
        n_averages = self._last_data["n_averages"]
        raw_amplitude = self.raw_amplitude_check.isChecked()

        self.plot.clear()
        self.plot.addItem(self._crosshair_v, ignoreBounds=True)
        self.plot.addItem(self._crosshair_h, ignoreBounds=True)
        self._crosshair_v.hide()
        self._crosshair_h.hide()

        fg = "#eeeeee" if self.dark_mode else "#222222"
        distances_arr = np.asarray(distances, dtype=np.float64)
        spacing = float(np.median(np.diff(distances_arr))) if len(distances_arr) > 1 else 1.0
        spacing = max(spacing, 1.0)
        trace_pen = pg.mkPen("#dddddd" if self.dark_mode else "#222222", width=1)
        arrival_pen = pg.mkPen("#ff5555" if self.dark_mode else "#d62728", width=2)

        # En modo "amplitud real" todas las trazas comparten el mismo factor
        # de escala (el pico global), asi se ve la atenuacion con la
        # distancia en vez de que cada traza se normalice a su propio pico.
        global_peak = 1.0
        if raw_amplitude:
            with np.errstate(invalid="ignore"):
                peaks = [np.nanmax(np.abs(signal)) for signal in matrix if np.any(np.isfinite(signal))]
            global_peak = float(np.nanmax(peaks)) if peaks else 1.0
            global_peak = global_peak or 1.0

        for distance, signal in zip(distances, matrix):
            if raw_amplitude:
                peak = global_peak
            else:
                with np.errstate(invalid="ignore"):
                    peak = float(np.nanmax(np.abs(signal))) if np.any(np.isfinite(signal)) else 1.0
                peak = peak or 1.0
            _plot_finite_segments(self.plot, common_time, signal / peak * spacing * 0.4 + distance, trace_pen)
            label_item = pg.TextItem(format_distance_label(distance), color=fg, anchor=(1.0, 0.5))
            label_item.setPos(float(common_time[0]), float(distance))
            self.plot.addItem(label_item)
            arrival = arrivals.get(format_distance_label(distance)) if arrivals else None
            if arrival is not None and arrival.reviewed and common_time[0] <= arrival.arrival_s <= common_time[-1]:
                self.plot.plot(
                    [arrival.arrival_s, arrival.arrival_s],
                    [distance - spacing * 0.35, distance + spacing * 0.35],
                    pen=arrival_pen,
                )

        hammer_added = False
        if hammer_global:
            try:
                hammer_time, hammer_mean = hammer_global_time_signal(hammer_global)
            except Exception:
                hammer_time = None
            if hammer_time is not None:
                finite = np.isfinite(hammer_mean)
                interp = (
                    np.interp(common_time, hammer_time[finite], hammer_mean[finite], left=np.nan, right=np.nan)
                    if np.any(finite)
                    else np.full(common_time.shape, np.nan)
                )
                with np.errstate(invalid="ignore"):
                    peak = float(np.nanmax(np.abs(interp))) if np.any(np.isfinite(interp)) else 1.0
                peak = peak or 1.0
                base = float(min(distances)) - spacing
                pen = pg.mkPen("#69b7ff" if self.dark_mode else "#0066cc", width=1.5)
                _plot_finite_segments(self.plot, common_time, interp / peak * spacing * 0.4 + base, pen)
                label_item = pg.TextItem(
                    f"hammer prom. (n={hammer_global.get('n', '?')})", color=fg, anchor=(1.0, 0.5)
                )
                label_item.setPos(float(common_time[0]), base)
                self.plot.addItem(label_item)
                hammer_added = True

        extra = " + hammer global" if hammer_added else ""
        mode = "amplitud real (atenuacion visible)" if raw_amplitude else "normalizada por traza"
        self.info_label.setText(f"{n_averages} promedios{extra} | escala: {mode}")

    def _send_to_masw(self) -> None:
        self._emit_masw(self.on_show_masw)

    def _send_to_auto_masw(self) -> None:
        self._emit_masw(self.on_auto_masw)

    def _emit_masw(self, callback) -> None:
        if self._last_data is None:
            QMessageBox.warning(self, "MASW", "Todavia no hay waterfall calculado.")
            return
        if callback is None:
            return
        common_time = self._last_data["common_time"]
        distances = self._last_data["distances"]
        matrix = self._last_data["matrix"]
        callback(common_time, distances, matrix)


class MaswPanel(QWidget):
    """Tab de analisis MASW con las tres etapas del flujo clasico:

    1. Dispersion: imagen frecuencia-velocidad (phase-shift, Park et al.
       1998) y picking de la curva de dispersion (auto + manual con click).
    2. Inversion: busqueda Monte Carlo del perfil de capas que mejor
       reproduce la curva picada (fast delta matrix como forward model).
    3. Perfil Vs: perfil de velocidad de corte vs profundidad resultante.

    Algoritmos en masw_dispersion.py / masw_inversion.py (puertos a numpy
    de third-party/maswavespy, sin depender de compilar Cython)."""

    def __init__(self, dark_mode: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.dark_mode = dark_mode
        self._raw_time: np.ndarray | None = None
        self._raw_distances: list[float] | None = None
        self._raw_matrix: np.ndarray | None = None
        self._last_result: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None
        self.picks: dict[float, float] = {}
        self._inv_result: dict | None = None
        self._abort_inversion = False
        self._inverting = False
        self._build_ui()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        self.inner_tabs = QTabWidget()
        outer.addWidget(self.inner_tabs)
        self.inner_tabs.addTab(self._build_dispersion_tab(), "1. Dispersion")
        self.inner_tabs.addTab(self._build_inversion_tab(), "2. Inversion")
        self.inner_tabs.addTab(self._build_profile_tab(), "3. Perfil Vs")
        self._apply_theme()

    def _build_dispersion_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.info_label = QLabel(
            "Sin datos todavia. Anda a la pestaña Waterfall y presiona 'Ver MASW'."
        )
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        controls = QHBoxLayout()
        self.cmin_spin = QDoubleSpinBox()
        self.cmin_spin.setRange(1.0, 5000.0)
        self.cmin_spin.setValue(50.0)
        self.cmin_spin.setSuffix(" m/s")
        self.cmax_spin = QDoubleSpinBox()
        self.cmax_spin.setRange(1.0, 5000.0)
        self.cmax_spin.setValue(600.0)
        self.cmax_spin.setSuffix(" m/s")
        self.cstep_spin = QDoubleSpinBox()
        self.cstep_spin.setRange(0.1, 100.0)
        self.cstep_spin.setValue(2.0)
        self.cstep_spin.setSuffix(" m/s")
        # Banda de frecuencias a evaluar. Defaults pensados para el geofono
        # usado en campo (respuesta util 1-200 Hz): f min recorta el ruido
        # por debajo de la banda del sensor y f max se puede subir hasta
        # 200 Hz si la cresta sigue viva (limite duro: Nyquist).
        self.fmin_spin = QDoubleSpinBox()
        self.fmin_spin.setRange(0.0, 1000.0)
        self.fmin_spin.setDecimals(1)
        self.fmin_spin.setValue(1.0)
        self.fmin_spin.setSuffix(" Hz")
        self.fmin_spin.setToolTip("Frecuencia minima de la imagen (banda del geofono: 1 Hz)")
        self.fmax_spin = QDoubleSpinBox()
        self.fmax_spin.setRange(1.0, 2000.0)
        self.fmax_spin.setValue(80.0)
        self.fmax_spin.setSuffix(" Hz")
        self.fmax_spin.setToolTip("Frecuencia maxima de la imagen (banda del geofono: 200 Hz)")
        self.calc_btn = QPushButton("Calcular imagen")
        self.calc_btn.clicked.connect(self._calculate)
        for label_text, widget in (
            ("c min", self.cmin_spin),
            ("c max", self.cmax_spin),
            ("paso c", self.cstep_spin),
            ("f min", self.fmin_spin),
            ("f max", self.fmax_spin),
        ):
            controls.addWidget(QLabel(label_text))
            controls.addWidget(widget)
        controls.addWidget(self.calc_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        pick_row = QHBoxLayout()
        self.pick_fmin_spin = QDoubleSpinBox()
        self.pick_fmin_spin.setRange(0.5, 2000.0)
        self.pick_fmin_spin.setValue(8.0)
        self.pick_fmin_spin.setSuffix(" Hz")
        self.pick_fmax_spin = QDoubleSpinBox()
        self.pick_fmax_spin.setRange(0.5, 2000.0)
        self.pick_fmax_spin.setValue(50.0)
        self.pick_fmax_spin.setSuffix(" Hz")
        self.autopick_btn = QPushButton("Auto-pick")
        self.autopick_btn.setToolTip(
            "Para cada frecuencia del rango, marca la velocidad de maxima amplitud (la cresta)."
        )
        self.autopick_btn.clicked.connect(self._auto_pick)
        self.edit_picks_check = QCheckBox("Editar con click (izq: agrega, der: borra)")
        self.clear_picks_btn = QPushButton("Limpiar picks")
        self.clear_picks_btn.clicked.connect(self._clear_picks)
        self.to_inversion_btn = QPushButton("Usar curva → Inversion")
        self.to_inversion_btn.clicked.connect(self._go_to_inversion)
        pick_row.addWidget(QLabel("Picking: f desde"))
        pick_row.addWidget(self.pick_fmin_spin)
        pick_row.addWidget(QLabel("hasta"))
        pick_row.addWidget(self.pick_fmax_spin)
        pick_row.addWidget(self.autopick_btn)
        pick_row.addWidget(self.edit_picks_check)
        pick_row.addWidget(self.clear_picks_btn)
        pick_row.addWidget(self.to_inversion_btn)
        pick_row.addStretch(1)
        layout.addLayout(pick_row)

        self._plot_item = pg.PlotItem()
        self.image_view = pg.ImageView(view=self._plot_item)
        self._plot_item.invertY(False)
        for axis_name in ("bottom", "left"):
            self._plot_item.getAxis(axis_name).enableAutoSIPrefix(False)
        try:
            self.image_view.ui.roiBtn.hide()
            self.image_view.ui.menuBtn.hide()
        except Exception:
            pass
        self.pick_scatter = pg.ScatterPlotItem(
            size=9, brush=pg.mkBrush("#ff3b30"), pen=pg.mkPen("#ffffff", width=1)
        )
        self.pick_scatter.setZValue(50)
        self._plot_item.addItem(self.pick_scatter)
        layout.addWidget(self.image_view, stretch=1)
        self.coord_label = QLabel("Move el mouse sobre la imagen para ver frecuencia / velocidad / amplitud")
        layout.addWidget(self.coord_label)
        self._plot_item.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._plot_item.scene().sigMouseClicked.connect(self._on_mouse_clicked)
        return tab

    def _build_inversion_tab(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        form_box = QGroupBox("Parametros de inversion")
        form = QFormLayout(form_box)
        self.nlayers_spin = QSpinBox()
        self.nlayers_spin.setRange(1, 8)
        self.nlayers_spin.setValue(3)
        self.niter_spin = QSpinBox()
        self.niter_spin.setRange(50, 20000)
        self.niter_spin.setValue(1000)
        self.bs_spin = QDoubleSpinBox()
        self.bs_spin.setRange(1.0, 30.0)
        self.bs_spin.setValue(8.0)
        self.bs_spin.setSuffix(" %")
        self.bh_spin = QDoubleSpinBox()
        self.bh_spin.setRange(1.0, 40.0)
        self.bh_spin.setValue(12.0)
        self.bh_spin.setSuffix(" %")
        self.nu_spin = QDoubleSpinBox()
        self.nu_spin.setRange(0.15, 0.45)
        self.nu_spin.setSingleStep(0.05)
        self.nu_spin.setValue(0.35)
        self.rho_spin = QDoubleSpinBox()
        self.rho_spin.setRange(1000.0, 2600.0)
        self.rho_spin.setValue(1850.0)
        self.rho_spin.setSuffix(" kg/m3")
        form.addRow("Capas", self.nlayers_spin)
        form.addRow("Iteraciones", self.niter_spin)
        form.addRow("Busqueda Vs (bs)", self.bs_spin)
        form.addRow("Busqueda espesor (bh)", self.bh_spin)
        form.addRow("Poisson (nu)", self.nu_spin)
        form.addRow("Densidad", self.rho_spin)
        left_layout.addWidget(form_box)

        self.run_inv_btn = QPushButton("Correr inversion")
        self.run_inv_btn.clicked.connect(self._run_inversion)
        self.stop_inv_btn = QPushButton("Detener")
        self.stop_inv_btn.setEnabled(False)
        self.stop_inv_btn.clicked.connect(self._stop_inversion)
        left_layout.addWidget(self.run_inv_btn)
        left_layout.addWidget(self.stop_inv_btn)
        self.inv_status_label = QLabel(
            "Sin curva todavia. Marca picks en '1. Dispersion' y presiona 'Usar curva → Inversion'."
        )
        self.inv_status_label.setWordWrap(True)
        left_layout.addWidget(self.inv_status_label)
        left_layout.addStretch(1)

        # Loop de inversion en vivo, como el flujo clasico de MASW:
        # arriba el Earth (Vs) Model actualizandose contra el inicial,
        # abajo la curva de dispersion medida vs teorica del mejor modelo.
        right = QSplitter(Qt.Orientation.Vertical)
        self.earth_plot = pg.PlotWidget(title="Earth (Vs) Model")
        self.earth_plot.setLabel("bottom", "Vs [m/s]")
        self.earth_plot.setLabel("left", "Profundidad [m]")
        self.earth_plot.showGrid(x=True, y=True, alpha=0.25)
        self.earth_plot.invertY(True)
        self.earth_plot.addLegend(offset=(-10, 10))
        self.inv_plot = pg.PlotWidget(title="Dispersion Curve")
        self.inv_plot.setLabel("bottom", "Frecuencia [Hz]")
        self.inv_plot.setLabel("left", "Velocidad de fase [m/s]")
        self.inv_plot.showGrid(x=True, y=True, alpha=0.25)
        self.inv_plot.addLegend(offset=(-10, 10))
        right.addWidget(self.earth_plot)
        right.addWidget(self.inv_plot)
        right.setSizes([380, 380])

        layout.addWidget(left)
        layout.addWidget(right, stretch=1)
        return tab

    def _build_profile_tab(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.profile_summary = QLabel("Sin perfil todavia. Corre la inversion en '2. Inversion'.")
        self.profile_summary.setWordWrap(True)
        left_layout.addWidget(self.profile_summary)
        self.save_results_btn = QPushButton("Guardar resultados (CSV)")
        self.save_results_btn.clicked.connect(self._save_results)
        left_layout.addWidget(self.save_results_btn)
        left_layout.addStretch(1)

        self.profile_plot = pg.PlotWidget(title="Final Vs Model")
        self.profile_plot.setLabel("bottom", "Vs [m/s]")
        self.profile_plot.setLabel("left", "Profundidad [m]")
        self.profile_plot.showGrid(x=True, y=True, alpha=0.25)
        self.profile_plot.invertY(True)
        self.profile_plot.addLegend(offset=(-10, 10))

        layout.addWidget(left)
        layout.addWidget(self.profile_plot, stretch=1)
        return tab

    # ------------------------------------------------ etapa 1: dispersion

    def set_data(self, common_time: np.ndarray, distances: list[float], matrix: np.ndarray) -> None:
        t_trim, m_trim = common_finite_window(common_time, matrix, t_min=0.0)
        if t_trim.size < 8:
            QMessageBox.warning(
                self,
                "MASW",
                "El tramo comun a todas las distancias (sin NaN, desde t=0) es demasiado corto para calcular MASW.",
            )
            return
        self._raw_time = t_trim
        self._raw_distances = list(distances)
        self._raw_matrix = m_trim
        self._clear_picks()
        self.inner_tabs.setCurrentIndex(0)
        self.info_label.setText(
            f"Datos listos: {len(distances)} canales (distancias), ventana comun 0-{t_trim[-1]:.3f} s. "
            "Ajusta los parametros y presiona 'Calcular imagen'."
        )

    def run_auto(self, common_time: np.ndarray, distances: list[float], matrix: np.ndarray) -> None:
        """Flujo MASW completo sin interaccion: imagen con los parametros
        actuales de la pestaña (defaults pensados para el tendido de campo),
        auto-pick con filtros de calidad (umbral de amplitud adaptativo,
        limites fisicos del tendido, rechazo de outliers) e inversion Monte
        Carlo. Cada parametro elegido queda visible en los controles para
        poder revisarlo o repetir el proceso a mano."""
        self.set_data(common_time, distances, matrix)
        if self._raw_matrix is None:
            return
        self._calculate()
        if self._last_result is None:
            return
        f, c, A = self._last_result
        try:
            freqs, c_obs = auto_extract_dispersion_curve(
                f, c, A, np.asarray(self._raw_distances, dtype=np.float64)
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Auto inversion", str(exc))
            return
        self.picks = {float(fv): float(cv) for fv, cv in zip(freqs, c_obs)}
        self.pick_fmin_spin.setValue(float(freqs[0]))
        self.pick_fmax_spin.setValue(float(freqs[-1]))
        self._refresh_pick_scatter()
        self.info_label.setText(
            f"Auto-pick: {len(self.picks)} puntos coherentes en {freqs[0]:.1f}-{freqs[-1]:.1f} Hz "
            "(umbral de amplitud + limites del tendido + rechazo de outliers)."
        )
        QApplication.processEvents()
        self._go_to_inversion()
        QApplication.processEvents()
        self._run_inversion()
        if self._inv_result is not None:
            self.inner_tabs.setCurrentIndex(2)
            self.profile_summary.setText(
                "AUTO INVERSION\n"
                f"Picks: {len(self.picks)} ({freqs[0]:.1f}-{freqs[-1]:.1f} Hz)\n"
                f"Capas: {int(self.nlayers_spin.value())} | iteraciones: {int(self.niter_spin.value())}\n\n"
                + self.profile_summary.text()
            )

    def _calculate(self) -> None:
        if self._raw_matrix is None:
            QMessageBox.warning(self, "MASW", "Todavia no hay datos. Anda a la pestaña Waterfall y presiona 'Ver MASW'.")
            return
        if self.cmax_spin.value() <= self.cmin_spin.value():
            QMessageBox.warning(self, "MASW", "c max debe ser mayor que c min.")
            return
        if self.fmax_spin.value() <= self.fmin_spin.value():
            QMessageBox.warning(self, "MASW", "f max debe ser mayor que f min.")
            return
        self.calc_btn.setEnabled(False)
        self.info_label.setText("Calculando imagen de dispersion (metodo phase-shift)...")
        QApplication.processEvents()
        try:
            u = self._raw_matrix.T  # (n_time, n_channels)
            fs = 1.0 / float(np.median(np.diff(self._raw_time)))
            f, c, A = phase_shift_dispersion_image(
                u,
                np.asarray(self._raw_distances, dtype=np.float64),
                fs,
                c_min=float(self.cmin_spin.value()),
                c_max=float(self.cmax_spin.value()),
                c_step=float(self.cstep_spin.value()),
                f_max=float(self.fmax_spin.value()),
                f_min=float(self.fmin_spin.value()),
            )
        except Exception as exc:
            QMessageBox.critical(self, "MASW", f"No se pudo calcular: {exc}")
            self.calc_btn.setEnabled(True)
            return
        self._last_result = (f, c, A)
        df = float(f[1] - f[0]) if len(f) > 1 else 1.0
        dc = float(c[1] - c[0]) if len(c) > 1 else 1.0
        self.image_view.setImage(A, pos=[float(f[0]), float(c[0])], scale=[df, dc], autoRange=True)
        self._plot_item.setLabel("bottom", "Frecuencia [Hz]")
        self._plot_item.setLabel("left", "Velocidad de fase [m/s]")
        try:
            self.image_view.setColorMap(pg.colormap.get("viridis"))
        except Exception:
            pass
        self._refresh_pick_scatter()
        self.info_label.setText(
            f"Imagen lista: {len(self._raw_distances)} canales, f<={f[-1]:.1f} Hz, "
            f"c=[{c[0]:.0f},{c[-1]:.0f}] m/s. Ahora marca la cresta: 'Auto-pick' o click manual."
        )
        self.calc_btn.setEnabled(True)

    def _auto_pick(self) -> None:
        if self._last_result is None:
            QMessageBox.warning(self, "MASW", "Primero calcula la imagen de dispersion.")
            return
        f, c, A = self._last_result
        f_lo = float(self.pick_fmin_spin.value())
        f_hi = float(self.pick_fmax_spin.value())
        added = 0
        for i, f_val in enumerate(f):
            if f_val < f_lo or f_val > f_hi or f_val <= 0:
                continue
            j = int(np.argmax(A[i]))
            self.picks[float(f_val)] = float(c[j])
            added += 1
        if not added:
            QMessageBox.warning(self, "MASW", "Ningun bin de frecuencia cae en el rango de picking elegido.")
            return
        self._refresh_pick_scatter()
        self.info_label.setText(
            f"{len(self.picks)} picks marcados. Revisalos (la cresta debe seguirse suave); "
            "corregi con click y despues 'Usar curva → Inversion'."
        )

    def _clear_picks(self) -> None:
        self.picks = {}
        self._refresh_pick_scatter()

    def _refresh_pick_scatter(self) -> None:
        if self.picks:
            fs = sorted(self.picks)
            self.pick_scatter.setData([float(v) for v in fs], [self.picks[v] for v in fs])
        else:
            self.pick_scatter.setData([], [])

    def _on_mouse_clicked(self, event) -> None:
        if not self.edit_picks_check.isChecked() or self._last_result is None:
            return
        if not self._plot_item.sceneBoundingRect().contains(event.scenePos()):
            return
        point = self._plot_item.vb.mapSceneToView(event.scenePos())
        f_val, c_val = float(point.x()), float(point.y())
        f, c, _A = self._last_result
        if event.button() == Qt.MouseButton.LeftButton:
            if not (f[0] <= f_val <= f[-1]) or f_val <= 0:
                return
            idx = int(np.argmin(np.abs(f - f_val)))
            self.picks[float(f[idx])] = float(np.clip(c_val, c[0], c[-1]))
            self._refresh_pick_scatter()
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            if not self.picks:
                return
            keys = list(self.picks)
            nearest = min(keys, key=lambda k: abs(k - f_val))
            del self.picks[nearest]
            self._refresh_pick_scatter()
            event.accept()

    def _on_mouse_moved(self, pos) -> None:
        if not self._plot_item.sceneBoundingRect().contains(pos):
            return
        point = self._plot_item.vb.mapSceneToView(pos)
        f_val, c_val = float(point.x()), float(point.y())
        amp_txt = ""
        if self._last_result is not None:
            f, c, A = self._last_result
            if f.size and c.size and f[0] <= f_val <= f[-1] and c[0] <= c_val <= c[-1]:
                df = float(f[1] - f[0]) if f.size > 1 else 1.0
                dc = float(c[1] - c[0]) if c.size > 1 else 1.0
                i = int(np.clip(round((f_val - f[0]) / df), 0, f.size - 1))
                j = int(np.clip(round((c_val - c[0]) / dc), 0, c.size - 1))
                amp_txt = f"   |   amplitud = {A[i, j]:.4f}"
        self.coord_label.setText(f"f = {f_val:.2f} Hz   |   c = {c_val:.1f} m/s{amp_txt}")

    # ------------------------------------------------- etapa 2: inversion

    def _picked_curve(self) -> tuple[np.ndarray, np.ndarray] | None:
        if len(self.picks) < 3:
            return None
        fs = np.array(sorted(self.picks), dtype=np.float64)
        cs = np.array([self.picks[v] for v in fs], dtype=np.float64)
        return fs, cs

    def _go_to_inversion(self) -> None:
        curve = self._picked_curve()
        if curve is None:
            QMessageBox.warning(self, "MASW", "Se necesitan al menos 3 picks para invertir.")
            return
        freqs, c_obs = curve
        self.inner_tabs.setCurrentIndex(1)
        self._plot_observed_curve(freqs, c_obs)
        self.inv_status_label.setText(
            f"Curva cargada: {len(freqs)} puntos, f=[{freqs[0]:.1f},{freqs[-1]:.1f}] Hz, "
            f"c=[{c_obs.min():.0f},{c_obs.max():.0f}] m/s. Ajusta parametros y corre la inversion."
        )

    def _plot_observed_curve(self, freqs: np.ndarray, c_obs: np.ndarray) -> None:
        self.inv_plot.clear()
        obs_pen = pg.mkPen(None)
        self.inv_plot.plot(
            freqs,
            c_obs,
            pen=obs_pen,
            symbol="o",
            symbolSize=7,
            symbolBrush="#ff3b30",
            name="Curva experimental (picks)",
        )

    def _stop_inversion(self) -> None:
        self._abort_inversion = True

    def _run_inversion(self) -> None:
        if self._inverting:
            return
        curve = self._picked_curve()
        if curve is None:
            QMessageBox.warning(self, "MASW", "Se necesitan al menos 3 picks (pestaña '1. Dispersion').")
            return
        freqs, c_obs = curve
        self._inverting = True
        self._abort_inversion = False
        self.run_inv_btn.setEnabled(False)
        self.stop_inv_btn.setEnabled(True)

        # Items persistentes para el loop en vivo: medida (puntos), modelo
        # inicial (gris punteado) y mejor modelo actual (verde) tanto en la
        # curva de dispersion como en el Earth (Vs) Model.
        self._plot_observed_curve(freqs, c_obs)
        self.earth_plot.clear()
        dashed = pg.mkPen("#888888", width=1.5, style=Qt.PenStyle.DashLine)
        green = pg.mkPen("#2ecc71", width=2.5)
        initial_dc_item = self.inv_plot.plot([], [], pen=dashed, name="Modelo inicial")
        best_dc_item = self.inv_plot.plot([], [], pen=green, name="Mejor modelo")
        initial_earth_item = self.earth_plot.plot([], [], pen=dashed, name="Modelo inicial")
        best_earth_item = self.earth_plot.plot([], [], pen=green, name="Mejor modelo")

        def update_items(best: dict, is_initial: bool) -> None:
            c_t = best["c_t"]
            valid = np.isfinite(c_t)
            z_total = float(np.sum(best["h"]))
            z_max = max(z_total * 1.4, z_total + 2.0)
            xs, ys = self._profile_steps(best["beta"], best["h"], z_max)
            if is_initial:
                if np.any(valid):
                    initial_dc_item.setData(freqs[valid], c_t[valid])
                initial_earth_item.setData(xs, ys)
            if np.any(valid):
                best_dc_item.setData(freqs[valid], c_t[valid])
            best_earth_item.setData(xs, ys)

        def progress(iteration: int, total: int, best_misfit: float, best: dict) -> bool:
            update_items(best, is_initial=(iteration == 0))
            if iteration == 0:
                self.inv_status_label.setText(f"Modelo inicial: desajuste {best_misfit:.2f} %")
            else:
                self.inv_status_label.setText(
                    f"Iteracion {iteration}/{total} | mejor desajuste: {best_misfit:.2f} %"
                )
            QApplication.processEvents()
            return not self._abort_inversion

        try:
            result = monte_carlo_inversion(
                freqs,
                c_obs,
                n_layers=int(self.nlayers_spin.value()),
                n_iterations=int(self.niter_spin.value()),
                bs=float(self.bs_spin.value()),
                bh=float(self.bh_spin.value()),
                nu=float(self.nu_spin.value()),
                rho=float(self.rho_spin.value()),
                progress_cb=progress,
            )
        except Exception as exc:
            QMessageBox.critical(self, "MASW", f"La inversion fallo: {exc}")
            return
        finally:
            self._inverting = False
            self.run_inv_btn.setEnabled(True)
            self.stop_inv_btn.setEnabled(False)

        self._inv_result = result
        update_items({"beta": result["beta"], "h": result["h"], "c_t": result["c_t"]}, is_initial=False)
        stopped = " (detenida)" if self._abort_inversion else ""
        self.inv_status_label.setText(
            f"Inversion terminada{stopped}: desajuste {result['misfit']:.2f} % "
            f"({len(result['history'])} iteraciones). El perfil final esta en '3. Perfil Vs'."
        )
        self._update_profile_tab()

    # ------------------------------------------------- etapa 3: perfil Vs

    @staticmethod
    def _profile_steps(beta: np.ndarray, h: np.ndarray, z_max: float) -> tuple[list[float], list[float]]:
        xs: list[float] = []
        ys: list[float] = []
        boundaries = np.concatenate(([0.0], np.cumsum(h)))
        for i, b in enumerate(beta):
            z0 = boundaries[i]
            z1 = boundaries[i + 1] if i < h.size else z_max
            xs += [float(b), float(b)]
            ys += [float(z0), float(z1)]
        return xs, ys

    def _update_profile_tab(self) -> None:
        result = self._inv_result
        if result is None:
            return
        beta = result["beta"]
        h = result["h"]
        z_total = float(np.sum(h))
        z_max = max(z_total * 1.4, z_total + 2.0)

        self.profile_plot.clear()
        xs0, ys0 = self._profile_steps(result["beta_initial"], result["h_initial"], z_max)
        self.profile_plot.plot(
            xs0, ys0, pen=pg.mkPen("#888888", width=1.5, style=Qt.PenStyle.DashLine), name="Modelo inicial"
        )
        xs, ys = self._profile_steps(beta, h, z_max)
        self.profile_plot.plot(xs, ys, pen=pg.mkPen("#2ecc71", width=2.5), name="Mejor modelo")

        lines = [f"Desajuste: {result['misfit']:.2f} %", ""]
        boundaries = np.concatenate(([0.0], np.cumsum(h)))
        for i in range(h.size):
            lines.append(
                f"Capa {i + 1}: {boundaries[i]:.2f}-{boundaries[i + 1]:.2f} m | "
                f"Vs = {beta[i]:.0f} m/s"
            )
        lines.append(f"Semiespacio (>{boundaries[-1]:.2f} m): Vs = {beta[-1]:.0f} m/s")
        self.profile_summary.setText("\n".join(lines))

    def _save_results(self) -> None:
        if self._inv_result is None:
            QMessageBox.warning(self, "MASW", "Todavia no hay resultados de inversion para guardar.")
            return
        selected = QFileDialog.getExistingDirectory(self, "Carpeta para guardar resultados MASW")
        if not selected:
            return
        out = Path(selected)
        result = self._inv_result
        curve_csv = out / "masw_curva_dispersion.csv"
        with curve_csv.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["freq_hz", "c_obs_m_s", "lambda_m", "c_teorica_m_s"])
            for f_val, c_o, lam, c_t in zip(
                result["freqs"], result["c_obs"], result["wavelengths"], result["c_t"]
            ):
                writer.writerow([f"{f_val:.4f}", f"{c_o:.2f}", f"{lam:.3f}", f"{c_t:.2f}" if np.isfinite(c_t) else ""])
        profile_csv = out / "masw_perfil_vs.csv"
        boundaries = np.concatenate(([0.0], np.cumsum(result["h"])))
        with profile_csv.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["capa", "z_techo_m", "z_piso_m", "espesor_m", "vs_m_s", "vp_m_s", "nu", "misfit_pct"])
            for i in range(result["h"].size):
                writer.writerow(
                    [
                        i + 1,
                        f"{boundaries[i]:.3f}",
                        f"{boundaries[i + 1]:.3f}",
                        f"{result['h'][i]:.3f}",
                        f"{result['beta'][i]:.1f}",
                        f"{result['alpha'][i]:.1f}",
                        f"{result['nu']:.2f}",
                        f"{result['misfit']:.3f}",
                    ]
                )
            writer.writerow(
                [
                    "semiespacio",
                    f"{boundaries[-1]:.3f}",
                    "",
                    "",
                    f"{result['beta'][-1]:.1f}",
                    f"{result['alpha'][-1]:.1f}",
                    f"{result['nu']:.2f}",
                    f"{result['misfit']:.3f}",
                ]
            )
        QMessageBox.information(
            self,
            "MASW",
            f"Guardado:\n{curve_csv}\n{profile_csv}",
        )

    # ------------------------------------------------------------- tema

    def set_dark_mode(self, dark: bool) -> None:
        self.dark_mode = bool(dark)
        self._apply_theme()

    def _apply_theme(self) -> None:
        bg = "#15181d" if self.dark_mode else "#ffffff"
        fg = "#eeeeee" if self.dark_mode else "#222222"
        try:
            self.image_view.ui.graphicsView.setBackground(bg)
        except Exception:
            pass
        for axis_name in ("bottom", "left"):
            axis = self._plot_item.getAxis(axis_name)
            axis.setPen(pg.mkPen(fg))
            axis.setTextPen(pg.mkPen(fg))
        for plot, title in (
            (self.earth_plot, "Earth (Vs) Model"),
            (self.inv_plot, "Dispersion Curve"),
            (self.profile_plot, "Final Vs Model"),
        ):
            plot.setBackground(bg)
            plot.setTitle(title, color=fg)
            for axis_name in ("bottom", "left"):
                axis = plot.getAxis(axis_name)
                axis.setPen(pg.mkPen(fg))
                axis.setTextPen(pg.mkPen(fg))


