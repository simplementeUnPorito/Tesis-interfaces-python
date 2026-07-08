"""PyQt reviewer for field hammer/geophone picks."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
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
    QListWidget,
    QListWidgetItem,
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
        FilterSettings,
        PickAnnotation,
        alignment_offsets_signature,
        alignment_shot_offsets_signature,
        annotations_signature,
        apply_bandpass_filter,
        auto_pick_shot,
        build_waterfall_matrix,
        compute_average_groups,
        default_alignment_offsets_path,
        default_alignment_shot_offsets_path,
        default_average_arrivals_path,
        default_filter_settings_path,
        default_masw_arrays_path,
        default_masw_state_path,
        default_output_dir,
        default_session_path,
        export_processed,
        filter_settings_signature,
        fk_directional_filter,
        format_distance_label,
        get_alignment_offset,
        hammer_global_time_signal,
        load_alignment_offsets,
        load_alignment_shot_offsets,
        load_annotations,
        load_average_arrivals,
        load_filter_settings,
        load_masw_arrays,
        load_masw_state,
        load_session,
        load_signal,
        peak_to_peak,
        resample_signal,
        segment_nan_padded,
        save_alignment_offsets,
        save_alignment_shot_offsets,
        save_annotations,
        save_average_arrivals,
        save_filter_settings,
        save_masw_arrays,
        save_masw_state,
        save_session,
    )
except ImportError:  # pragma: no cover - script execution from this folder
    from field_review_data import (
        AverageArrivalAnnotation,
        FieldDataset,
        FieldShot,
        FilterSettings,
        PickAnnotation,
        alignment_offsets_signature,
        alignment_shot_offsets_signature,
        annotations_signature,
        apply_bandpass_filter,
        auto_pick_shot,
        build_waterfall_matrix,
        compute_average_groups,
        default_alignment_offsets_path,
        default_alignment_shot_offsets_path,
        default_average_arrivals_path,
        default_filter_settings_path,
        default_masw_arrays_path,
        default_masw_state_path,
        default_output_dir,
        default_session_path,
        export_processed,
        filter_settings_signature,
        fk_directional_filter,
        format_distance_label,
        get_alignment_offset,
        hammer_global_time_signal,
        load_alignment_offsets,
        load_alignment_shot_offsets,
        load_annotations,
        load_average_arrivals,
        load_filter_settings,
        load_masw_arrays,
        load_masw_state,
        load_session,
        load_signal,
        peak_to_peak,
        resample_signal,
        segment_nan_padded,
        save_alignment_offsets,
        save_alignment_shot_offsets,
        save_annotations,
        save_average_arrivals,
        save_filter_settings,
        save_masw_arrays,
        save_masw_state,
        save_session,
    )

try:
    from .masw_dispersion import (
        auto_extract_dispersion_curve,
        common_finite_window,
        phase_shift_dispersion_image,
    )
    from .masw_inversion import monte_carlo_inversion
    from . import masw_multimodal
except ImportError:  # pragma: no cover - script execution from this folder
    from masw_dispersion import (
        auto_extract_dispersion_curve,
        common_finite_window,
        phase_shift_dispersion_image,
    )
    from masw_inversion import monte_carlo_inversion
    import masw_multimodal


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
    _ORDER_MODE_P2P = "Pico a pico (mayor primero)"
    _ORDER_MODE_ORIGINAL = "Carpeta / captura (original)"
    _OK_AVERAGE_COLOR = "#ff33cc"

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
        self.filter_settings_path = default_filter_settings_path(dataset.raw_root)
        self.filter_settings = load_filter_settings(self.filter_settings_path)
        self.alignment_offsets_path = default_alignment_offsets_path(dataset.raw_root)
        self.alignment_offsets = load_alignment_offsets(self.alignment_offsets_path)
        self.alignment_shot_offsets_path = default_alignment_shot_offsets_path(dataset.raw_root)
        self.alignment_shot_offsets = load_alignment_shot_offsets(self.alignment_shot_offsets_path)
        self.session_path = default_session_path(dataset.raw_root)
        self._session = load_session(self.session_path)
        self._session_last_shot_id = self._session.get("last_shot_id")
        self.masw_state_path = default_masw_state_path(dataset.raw_root)
        self.masw_arrays_path = default_masw_arrays_path(dataset.raw_root)
        self._masw_state = load_masw_state(self.masw_state_path)
        self._masw_arrays = load_masw_arrays(self.masw_arrays_path)
        self._ui_ready = False
        self._signal_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._p2p_cache: dict[str, float] = {}
        self._row_order: list[int] = list(range(len(self.dataset.shots)))
        self._shot_idx_to_row: dict[int, int] = {i: i for i in range(len(self.dataset.shots))}
        self._loading = False
        self._current_row = -1
        self._trigger_shortcuts: list[QShortcut] = []
        self.auto_search_window_s: tuple[float, float] | None = None
        self._marking_auto_zone = False
        self._auto_zone_clicks: list[float] = []
        self.auto_zone_lines: list[pg.InfiniteLine] = []
        self.dark_mode = bool(self._session.get("dark_mode", False))
        self.trigger_line: pg.InfiniteLine | None = None
        self.geo_trigger_line: pg.InfiniteLine | None = None

        for shot in self.dataset.shots:
            if shot.shot_id not in self.annotations:
                self.annotations[shot.shot_id] = self._safe_auto_pick(shot)

        self.setWindowTitle("Revision Canchita - hammer/geofono")
        self.resize(1380, 860)
        self._build_ui()
        self._restore_session_ui()
        self._reorder_rows(select=False)
        target = self._session_target_row()
        if target is not None:
            self.table.selectRow(target)
            self._select_row(target)
        self._restore_masw_state()
        self._ui_ready = True

    def _restore_masw_state(self) -> None:
        """Restaura el ultimo waterfall y analisis MASW guardados (picks por
        modo, regiones, resultado de inversion) para no rehacer todo el flujo
        al reabrir la app sobre el mismo dataset."""
        try:
            self.waterfall_panel.restore_state(
                self._masw_state.get("waterfall", {}),
                self._masw_arrays,
                self.average_panel.arrivals,
            )
            self.masw_panel.restore_state(
                self._masw_state.get("masw", {}),
                self._masw_arrays,
            )
        except Exception:
            pass

    def _save_masw_state(self) -> None:
        try:
            state = {
                "masw": self.masw_panel.get_state(),
                "waterfall": self.waterfall_panel.get_state(),
            }
            arrays = {}
            arrays.update(self.waterfall_panel.get_arrays())
            arrays.update(self.masw_panel.get_arrays())
            save_masw_state(self.masw_state_path, state)
            save_masw_arrays(self.masw_arrays_path, arrays)
        except Exception:
            pass

    def _restore_session_ui(self) -> None:
        """Reaplica orden/filtro guardados de la sesion anterior (el modo
        oscuro ya se aplico via self.dark_mode). Con las señales bloqueadas
        para no disparar recalculos intermedios; el _reorder_rows posterior
        hace el trabajo una sola vez."""
        order_mode = self._session.get("order_mode")
        if order_mode in (self._ORDER_MODE_P2P, self._ORDER_MODE_ORIGINAL):
            self.order_combo.blockSignals(True)
            self.order_combo.setCurrentText(order_mode)
            self.order_combo.blockSignals(False)
        filter_mode = self._session.get("filter_mode")
        if filter_mode:
            self.filter_combo.blockSignals(True)
            self.filter_combo.setCurrentText(str(filter_mode))
            self.filter_combo.blockSignals(False)
        filter_distance = self._session.get("filter_distance")
        if filter_distance is not None:
            self.filter_distance_spin.blockSignals(True)
            self.filter_distance_spin.setValue(float(filter_distance))
            self.filter_distance_spin.blockSignals(False)

    def _session_target_row(self) -> int | None:
        """Fila visible de la muestra donde se dejo la sesion anterior; si no
        existe o quedo oculta por el filtro, la primera visible."""
        if self._session_last_shot_id:
            for row, shot_idx in enumerate(self._row_order):
                if (
                    self.dataset.shots[shot_idx].shot_id == self._session_last_shot_id
                    and not self.table.isRowHidden(row)
                ):
                    return row
        visible = self._visible_rows()
        return visible[0] if visible else None

    def _save_session(self) -> None:
        if not self._ui_ready:
            return
        current = self._current()
        shot_id = current[0].shot_id if current is not None else self._session_last_shot_id
        self._session_last_shot_id = shot_id
        data = {
            "last_shot_id": shot_id,
            "order_mode": self.order_combo.currentText(),
            "filter_mode": self.filter_combo.currentText(),
            "filter_distance": float(self.filter_distance_spin.value()),
            "dark_mode": bool(self.dark_mode),
        }
        try:
            save_session(self.session_path, data)
        except Exception:
            pass

    def _autosave_annotations(self) -> None:
        """Persiste las marcas en disco sin ruido en el status (se llama
        despues de CUALQUIER cambio: estado, trigger, distancia, notas,
        inversion). No hace falta ningun boton de guardar."""
        try:
            save_annotations(self.annotations_path, self.dataset, self.annotations)
        except Exception:
            pass

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

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Estado", "Dist", "Trigger s", "Carpeta", "Captura", "Hash"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.currentCellChanged.connect(lambda row, *_: self._select_row(row))
        left_layout.addWidget(self.table, stretch=1)

        order_box = QHBoxLayout()
        self.order_combo = QComboBox()
        self.order_combo.addItems([self._ORDER_MODE_P2P, self._ORDER_MODE_ORIGINAL])
        self.order_combo.setToolTip(
            "Pico a pico: empieza por la señal más fácil de ver el golpe, para calibrar\n"
            "el resto contra esa. Carpeta/captura: orden original de adquisición."
        )
        self.order_combo.currentIndexChanged.connect(self._order_mode_changed)
        order_box.addWidget(QLabel("Orden"))
        order_box.addWidget(self.order_combo, stretch=1)
        left_layout.addLayout(order_box)

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
            "Teclas: W/S muestra ant/sig · A/D borde izq de zona auto · ←/→ borde der · "
            "↑/↓ zoom hammer (Shift = geo) · Espacio rota estado (sin validar→OK→rechazada) · "
            "X invierte la señal. El trigger se mueve arrastrando la línea naranja con el mouse. "
            "Todo se guarda solo; al reabrir seguís donde quedaste."
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
        self.auto_zone_btn = QPushButton("Marcar zona auto")
        self.auto_zone_btn.clicked.connect(self._start_auto_zone_marking)
        self.clear_auto_zone_btn = QPushButton("Limpiar zona")
        self.clear_auto_zone_btn.clicked.connect(self._clear_auto_zone)
        self.apply_folder_btn = QPushButton("Aplicar dist. a carpeta")
        self.apply_folder_btn.clicked.connect(self._apply_distance_to_folder)
        self.flip_geo_btn = QPushButton("Invertir geo de carpeta")
        self.flip_geo_btn.setToolTip(
            "El circuito del geofono no tiene polaridad: segun el dia pudo quedar conectado al reves.\n"
            "Invierte el geofono de TODAS las capturas de la carpeta actual (toggle, queda guardado en las marcas)."
        )
        self.flip_geo_btn.clicked.connect(self._flip_geo_folder)
        self.flip_geo_single_btn = QPushButton("Invertir esta señal")
        self.flip_geo_single_btn.setToolTip(
            "Invierte el geofono de SOLO la captura actual (toggle, queda guardado en su marca).\n"
            "A diferencia de 'Invertir geo de carpeta', no toca el resto de la carpeta."
        )
        self.flip_geo_single_btn.clicked.connect(self._flip_geo_single)
        nav.addWidget(self.prev_btn, 0, 0)
        nav.addWidget(self.next_btn, 0, 1)
        nav.addWidget(self.auto_btn, 1, 0)
        nav.addWidget(self.save_next_btn, 1, 1)
        nav.addWidget(self.auto_zone_btn, 2, 0)
        nav.addWidget(self.clear_auto_zone_btn, 2, 1)
        nav.addWidget(self.apply_folder_btn, 3, 0)
        nav.addWidget(self.flip_geo_btn, 3, 1)
        nav.addWidget(self.flip_geo_single_btn, 4, 0, 1, 2)
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
            filter_settings=self.filter_settings,
            alignment_offsets=self.alignment_offsets,
            alignment_shot_offsets=self.alignment_shot_offsets,
        )
        self.filter_panel = FilterPanel(
            settings=self.filter_settings,
            get_current=self._filter_preview_data,
            dark_mode=self.dark_mode,
            on_changed=self._filter_settings_changed,
        )
        self.alignment_panel = AlignmentPanel(
            dataset=self.dataset,
            annotations=self.annotations,
            offsets=self.alignment_offsets,
            shot_offsets=self.alignment_shot_offsets,
            get_zeroed=self._zeroed_pair,
            get_peak_to_peak=self._shot_peak_to_peak,
            dark_mode=self.dark_mode,
            on_changed=self._alignment_offsets_changed,
        )
        self.tabs.addTab(self.filter_panel, "Filtros")
        self.tabs.addTab(self.alignment_panel, "Enfase")
        self.tabs.addTab(self.average_panel, "Promedios / arrivals")
        self.tabs.addTab(self.waterfall_panel, "Waterfall")
        self.tabs.addTab(self.masw_panel, "MASW")
        self.tabs.currentChanged.connect(self._tab_changed)

        QApplication.instance().installEventFilter(self)
        self._apply_theme()

    def _tab_changed(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if widget is self.average_panel:
            self._save()
            self.average_panel.set_output_dir(self.output_dir)
            self.average_panel.refresh()
        elif widget is self.filter_panel:
            self.filter_panel.refresh_preview()
        elif widget is self.alignment_panel:
            self.alignment_panel.refresh()

    def _filter_preview_data(self) -> dict | None:
        current = self._current()
        if current is None:
            return None
        shot, ann = current
        fs = float(shot.fs or shot.geo.fs or shot.hammer.fs)
        if fs <= 0:
            return None
        hammer, geo = self._zeroed_pair(shot, ann)
        if geo.size == 0:
            return None
        return {
            "label": f"{shot.folder_name} / {shot.capture_name}",
            "fs": fs,
            "trigger_s": float(ann.trigger_s),
            "hammer": hammer,
            "geo": geo,
        }

    def _filter_settings_changed(self) -> None:
        try:
            save_filter_settings(self.filter_settings_path, self.filter_settings)
        except Exception:
            pass

    def _alignment_offsets_changed(self) -> None:
        try:
            save_alignment_offsets(self.alignment_offsets_path, self.alignment_offsets)
            save_alignment_shot_offsets(self.alignment_shot_offsets_path, self.alignment_shot_offsets)
        except Exception:
            pass

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

    def _compute_row_order(self) -> list[int]:
        indices = list(range(len(self.dataset.shots)))
        if self.order_combo.currentText() == self._ORDER_MODE_ORIGINAL:
            return indices
        return sorted(
            indices,
            key=lambda i: (-self._shot_peak_to_peak(self.dataset.shots[i]), i),
        )

    def _order_mode_changed(self, *_args) -> None:
        self._reorder_rows(select=True)
        self._save_session()

    def _reorder_rows(self, select: bool = True) -> None:
        """Recalcula self._row_order (y su inversa) y repuebla la tabla,
        preservando la seleccion actual por shot_id (la fila numerica
        cambia, la señal seleccionada no)."""
        current_shot_id = None
        if 0 <= self._current_row < len(self._row_order):
            current_shot_id = self.dataset.shots[self._row_order[self._current_row]].shot_id
        self._row_order = self._compute_row_order()
        self._shot_idx_to_row = {shot_idx: row for row, shot_idx in enumerate(self._row_order)}
        self._populate_table()
        if current_shot_id is not None:
            for row, shot_idx in enumerate(self._row_order):
                if self.dataset.shots[shot_idx].shot_id == current_shot_id:
                    self._current_row = row
                    break
        self._apply_filter(select=select, keep_current=True)

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self._row_order))
        for row, shot_idx in enumerate(self._row_order):
            self._update_table_row(row, self.dataset.shots[shot_idx])

    def _update_table_row(self, row: int, shot: FieldShot) -> None:
        ann = self.annotations.get(shot.shot_id)
        accepted = ann.accepted if ann else True
        dist = ann.distance_m if ann else shot.distance_m
        trigger_s = ann.trigger_s if ann else 0.0
        reviewed = bool(ann.reviewed) if ann else False
        estado, color_hex = self._estado_display(reviewed, accepted)
        values = [
            estado,
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
            item.setForeground(QColor(color_hex))
            item.setBackground(QColor("#24272e" if self.dark_mode else "#ffffff"))

    def _estado_display(self, reviewed: bool, accepted: bool) -> tuple[str, str]:
        """Estado derivado de (reviewed, accepted): toda marca nueva empieza
        "sin validar" (reviewed default False) y no cuenta para el promedio
        hasta que se revise a mano. Devuelve (texto, color hex)."""
        if not reviewed:
            return "Sin validar", "#c9a227" if self.dark_mode else "#8a6d00"
        if accepted:
            return "OK", "#3ddc84" if self.dark_mode else "#1a7f37"
        return "Rechazada", "#8a8f98" if self.dark_mode else "#777777"

    def _apply_filter(self, *_args, select: bool = True, keep_current: bool = True) -> None:
        visible: list[int] = []
        for row, shot_idx in enumerate(self._row_order):
            shot = self.dataset.shots[shot_idx]
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
        return [row for row in range(len(self._row_order)) if not self.table.isRowHidden(row)]

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
        if self._loading or row < 0 or row >= len(self._row_order):
            return
        if row != self._current_row and self.auto_search_window_s is not None:
            # La zona auto es de la muestra en la que se marco: al pasar a
            # otra muestra se limpia sola en vez de arrastrarse.
            self.auto_search_window_s = None
            self._marking_auto_zone = False
            self._auto_zone_clicks = []
        self._current_row = row
        shot = self.dataset.shots[self._row_order[row]]
        ann = self._annotation(shot)
        self._loading = True
        self.position_label.setText(f"{row + 1} / {len(self._row_order)}")
        self.distance_spin.setValue(float(ann.distance_m))
        self.accept_check.setChecked(bool(ann.accepted))
        self.notes_edit.setText(ann.notes)
        self._loading = False
        self._refresh_plot()
        self._update_labels()
        self._save_session()

    def _annotation(self, shot: FieldShot) -> PickAnnotation:
        ann = self.annotations.get(shot.shot_id)
        if ann is None:
            ann = self._safe_auto_pick(shot)
            self.annotations[shot.shot_id] = ann
        return ann

    def _current(self) -> tuple[FieldShot, PickAnnotation] | None:
        if self._current_row < 0 or self._current_row >= len(self._row_order):
            return None
        shot = self.dataset.shots[self._row_order[self._current_row]]
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
        if cached is None:
            hammer = load_signal(shot.hammer, prefer_filtered=self.prefer_filtered, apply_invert=True)
            geo = load_signal(shot.geo, prefer_filtered=self.prefer_filtered, apply_invert=True)
            n = min(hammer.size, geo.size)
            cached = (hammer[:n], geo[:n])
            self._signal_cache[shot.shot_id] = cached
        hammer, geo = cached
        # La cache guarda el geo sin el flip por muestra; se aplica aca para
        # que el boton de invertir no tenga que invalidar nada.
        ann = self.annotations.get(shot.shot_id)
        if ann is not None and ann.geo_flip:
            geo = -geo
        return hammer, geo

    def _shot_peak_to_peak(self, shot: FieldShot) -> float:
        """Pico a pico del canal geo (max-min), cacheado por shot_id.

        No depende de geo_flip: invertir el signo de una senal no cambia su
        max-min, asi que el cache no se invalida cuando el usuario invierte
        una carpeta o una senal individual."""
        cached = self._p2p_cache.get(shot.shot_id)
        if cached is None:
            _hammer, geo = self._load_pair(shot)
            cached = peak_to_peak(geo)
            self._p2p_cache[shot.shot_id] = cached
        return cached

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
        self._plot_ok_average(ann)
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

    def _ok_average_for_distance(self, distance_m: float) -> tuple[np.ndarray, np.ndarray] | None:
        """Promedio en vivo (alineado por trigger) del geofono de las
        señales YA marcadas OK (accepted+reviewed) en la misma distancia que
        `distance_m`. None si todavia no hay ninguna. Version liviana de
        `compute_average_groups`: reusa el cache de señales ya cargadas
        (`_load_pair`/`_zeroed_pair`) en vez de releer del disco, y no
        resamplea entre fs distintas (si aparece un fs distinto dentro del
        mismo grupo se ignora en este preview; el promedio "de verdad" para
        exportar/waterfall si mezcla fs, ver `compute_average_groups`)."""
        target = round(float(distance_m), 3)
        segments: list[tuple[np.ndarray, int]] = []
        fs_ref: float | None = None
        for shot in self.dataset.shots:
            ann = self.annotations.get(shot.shot_id)
            if ann is None or not ann.accepted or not ann.reviewed:
                continue
            if round(float(ann.distance_m), 3) != target:
                continue
            fs = float(shot.fs or shot.geo.fs or shot.hammer.fs)
            if fs <= 0:
                continue
            if fs_ref is None:
                fs_ref = fs
            elif abs(fs - fs_ref) > 1e-6:
                continue
            _hammer, geo = self._zeroed_pair(shot, ann)
            if geo.size == 0:
                continue
            trigger_idx = int(round(float(ann.trigger_s) * fs))
            segments.append((geo, trigger_idx))
        if not segments or fs_ref is None:
            return None
        rel_start = max(-idx for _geo, idx in segments)
        rel_end = max(geo.size - idx for geo, idx in segments)
        if rel_end <= rel_start + 1:
            return None
        stack = [
            segment_nan_padded(geo, idx + rel_start, idx + rel_end)
            for geo, idx in segments
        ]
        with np.errstate(invalid="ignore"):
            mean = np.nanmean(np.vstack(stack), axis=0)
        time_s = np.arange(rel_start, rel_end, dtype=np.float64) / fs_ref
        return time_s, mean

    def _plot_ok_average(self, current_ann: PickAnnotation) -> None:
        result = self._ok_average_for_distance(current_ann.distance_m)
        if result is None:
            return
        time_s, mean = result
        pen = pg.mkPen(self._OK_AVERAGE_COLOR, width=3)
        item = self.geo_plot.plot(time_s, mean, pen=pen, name="promedio OK")
        item.setZValue(30)

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
        self._autosave_annotations()

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
        self._autosave_annotations()

    def _nudge_trigger_by_samples(self, direction: int, samples: int) -> None:
        current = self._current()
        if current is None:
            return
        shot, _ann = current
        fs = shot.fs or shot.geo.fs or shot.hammer.fs
        if fs <= 0:
            return
        self._nudge_trigger(direction, float(samples) / fs)

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
        self._autosave_annotations()

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
        self._refresh_plot()
        self._autosave_annotations()

    def _notes_changed(self) -> None:
        current = self._current()
        if current is None:
            return
        _shot, ann = current
        ann.notes = self.notes_edit.text()
        ann.source = "manual"
        self._autosave_annotations()

    def _toggle_theme(self) -> None:
        self.dark_mode = not self.dark_mode
        self._apply_theme()
        self._refresh_plot()
        self._save_session()

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
        for row, shot_idx in enumerate(self._row_order):
            self._update_table_row(row, self.dataset.shots[shot_idx])
        if hasattr(self, "average_panel"):
            self.average_panel.set_dark_mode(self.dark_mode)
        if hasattr(self, "waterfall_panel"):
            self.waterfall_panel.set_dark_mode(self.dark_mode)
        if hasattr(self, "masw_panel"):
            self.masw_panel.set_dark_mode(self.dark_mode)
        if hasattr(self, "filter_panel"):
            self.filter_panel.set_dark_mode(self.dark_mode)
        if hasattr(self, "alignment_panel"):
            self.alignment_panel.set_dark_mode(self.dark_mode)

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

    # ---------------------------------------------------------- teclado
    # Esquema (solo en la pestaña Capturas, con la ventana activa y sin foco
    # en un campo de texto/spin):
    #   W / S      muestra anterior / siguiente
    #   A / D      borde IZQUIERDO de la zona auto  (- / +)
    #   ← / →      borde DERECHO de la zona auto     (- / +)
    #   ↑ / ↓      zoom in / out del grafico HAMMER
    #   Shift+↑/↓  zoom in / out del grafico GEO
    #   Espacio    rota el estado (sin validar → OK → rechazada)
    #   X          invierte la señal actual (geo_flip)
    # Se usa un event filter a nivel app para poder ganarle al manejo nativo
    # de flechas de la tabla/plots, pero respetando los campos de texto.
    _ZONE_NUDGE_SAMPLES = 3

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if event.type() == QEvent.Type.KeyPress and self._handle_review_key(event):
            return True
        return super().eventFilter(obj, event)

    def _handle_review_key(self, event) -> bool:
        if not getattr(self, "_ui_ready", False):
            return False
        if not self.isActiveWindow():
            return False
        if self.tabs.currentWidget() is not self.review_root:
            return False
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox)):
            return False
        key = event.key()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if key == Qt.Key.Key_W:
            self._move_row(-1)
        elif key == Qt.Key.Key_S:
            self._move_row(1)
        elif key == Qt.Key.Key_A:
            self._nudge_auto_zone("left", -1)
        elif key == Qt.Key.Key_D:
            self._nudge_auto_zone("left", 1)
        elif key == Qt.Key.Key_Left:
            self._nudge_auto_zone("right", -1)
        elif key == Qt.Key.Key_Right:
            self._nudge_auto_zone("right", 1)
        elif key == Qt.Key.Key_Up:
            self._zoom_plot("geo" if shift else "hammer", zoom_in=True)
        elif key == Qt.Key.Key_Down:
            self._zoom_plot("geo" if shift else "hammer", zoom_in=False)
        elif key == Qt.Key.Key_Space:
            self._cycle_estado()
        elif key == Qt.Key.Key_X:
            self._flip_geo_single()
        else:
            return False
        return True

    def _zoom_plot(self, which: str, zoom_in: bool) -> None:
        plot = self.hammer_plot if which == "hammer" else self.geo_plot
        factor = 0.8 if zoom_in else 1.25
        plot.getViewBox().scaleBy((factor, factor))

    def _cycle_estado(self) -> None:
        """Rota el estado de la muestra actual: sin validar → OK → rechazada
        → sin validar. Desde el default (sin validar) el primer Espacio la
        marca OK, que es lo mas comun."""
        current = self._current()
        if current is None:
            return
        shot, ann = current
        if not ann.reviewed:
            idx = 0
        elif ann.accepted:
            idx = 1
        else:
            idx = 2
        states = [(False, True), (True, True), (True, False)]  # sin validar, OK, rechazada
        ann.reviewed, ann.accepted = states[(idx + 1) % 3]
        ann.source = "manual"
        self._loading = True
        self.accept_check.setChecked(bool(ann.accepted))
        self._loading = False
        self._update_table_row(self._current_row, shot)
        self._refresh_plot()
        self._autosave_annotations()
        estado, _color = self._estado_display(ann.reviewed, ann.accepted)
        self.status_label.setText(f"{shot.folder_name}/{shot.capture_name}: {estado}")

    def _nudge_auto_zone(self, edge: str, direction: int) -> None:
        """Mueve un borde de la zona auto (la ventana donde 'Auto' busca el
        golpe). A/D = borde izquierdo, ←/→ = borde derecho. Si todavia no hay
        zona, la crea alrededor del trigger actual."""
        current = self._current()
        if current is None:
            return
        shot, ann = current
        fs = shot.fs or shot.geo.fs or shot.hammer.fs
        if fs <= 0:
            return
        hammer, _geo = self._load_pair(shot)
        max_s = (hammer.size - 1) / fs if hammer.size else 0.0
        step_s = self._ZONE_NUDGE_SAMPLES / fs
        if self.auto_search_window_s is None:
            half = 0.05
            a = max(0.0, float(ann.trigger_s) - half)
            b = min(max_s, float(ann.trigger_s) + half)
            if b <= a:
                b = min(max_s, a + step_s * 4)
            self.auto_search_window_s = (a, b)
        a, b = sorted(self.auto_search_window_s)
        if edge == "left":
            a = a + direction * step_s
        else:
            b = b + direction * step_s
        a = max(0.0, min(a, max_s))
        b = max(0.0, min(b, max_s))
        if a >= b:
            if edge == "left":
                a = max(0.0, b - step_s)
            else:
                b = min(max_s, a + step_s)
        self.auto_search_window_s = (a, b)
        self._refresh_plot()
        self.status_label.setText(f"Zona auto: {a:.4f}s a {b:.4f}s (A/D borde izq, ←/→ borde der)")

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
        self._autosave_annotations()

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
        for shot_idx, other in enumerate(self.dataset.shots):
            if other.folder_name != shot.folder_name:
                continue
            other_ann = self._annotation(other)
            other_ann.distance_m = distance
            other_ann.source = "manual"
            self._update_table_row(self._shot_idx_to_row[shot_idx], other)
            changed += 1
        self.status_label.setText(f"Distancia {distance:.3f} m aplicada a {changed} capturas")
        self._apply_filter(keep_current=True)
        self._refresh_plot()
        self._autosave_annotations()

    def _flip_geo_folder(self) -> None:
        current = self._current()
        if current is None:
            return
        shot, ann = current
        new_flip = not bool(ann.geo_flip)
        changed = 0
        for other in self.dataset.shots:
            if other.folder_name != shot.folder_name:
                continue
            other_ann = self._annotation(other)
            other_ann.geo_flip = new_flip
            other_ann.source = "manual"
            changed += 1
        estado = "INVERTIDO" if new_flip else "normal"
        self._refresh_plot()
        self._autosave_annotations()
        self.status_label.setText(
            f"Geofono {estado} en {changed} capturas de {shot.folder_name}"
        )

    def _flip_geo_single(self) -> None:
        current = self._current()
        if current is None:
            return
        shot, ann = current
        ann.geo_flip = not bool(ann.geo_flip)
        ann.source = "manual"
        self._refresh_plot()
        self._autosave_annotations()
        estado = "INVERTIDO" if ann.geo_flip else "normal"
        self.status_label.setText(
            f"Geofono {estado} solo en {shot.folder_name}/{shot.capture_name}"
        )

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
            save_filter_settings(self.filter_settings_path, self.filter_settings)
            save_alignment_offsets(self.alignment_offsets_path, self.alignment_offsets)
            save_alignment_shot_offsets(self.alignment_shot_offsets_path, self.alignment_shot_offsets)
            self._save_session()
            self._save_masw_state()
        finally:
            super().closeEvent(event)


class FilterPanel(QWidget):
    """Tab de filtrado: pasa-banda Butterworth con filtfilt (fase cero) y
    resampleo para combinar capturas con fs distinta (3 s @ 2929 Hz y
    10.59 s @ 1020 Hz). Los parametros se aplican a promedios, waterfall,
    MASW y export; aca solo se previsualiza sobre la captura seleccionada
    en la pestaña Capturas."""

    def __init__(
        self,
        settings: FilterSettings,
        get_current,
        dark_mode: bool = False,
        on_changed=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.get_current = get_current
        self.dark_mode = dark_mode
        self.on_changed = on_changed
        self._loading = False
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        root = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(root)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)

        box = QGroupBox("Filtro pasa-banda (Butterworth + filtfilt, fase cero)")
        form = QFormLayout(box)
        self.enable_check = QCheckBox("Aplicar a promedios / waterfall / MASW / export")
        self.enable_check.toggled.connect(self._settings_edited)
        self.low_spin = QDoubleSpinBox()
        self.low_spin.setRange(0.0, 5000.0)
        self.low_spin.setDecimals(2)
        self.low_spin.setSingleStep(0.5)
        self.low_spin.setSuffix(" Hz")
        self.low_spin.setSpecialValueText("sin corte bajo")
        self.low_spin.valueChanged.connect(self._settings_edited)
        self.high_spin = QDoubleSpinBox()
        self.high_spin.setRange(0.0, 5000.0)
        self.high_spin.setDecimals(2)
        self.high_spin.setSingleStep(5.0)
        self.high_spin.setSuffix(" Hz")
        self.high_spin.setSpecialValueText("sin corte alto")
        self.high_spin.valueChanged.connect(self._settings_edited)
        self.order_spin = QSpinBox()
        self.order_spin.setRange(1, 10)
        self.order_spin.valueChanged.connect(self._settings_edited)
        self.target_fs_spin = QDoubleSpinBox()
        self.target_fs_spin.setRange(0.0, 20000.0)
        self.target_fs_spin.setDecimals(1)
        self.target_fs_spin.setSingleStep(10.0)
        self.target_fs_spin.setSuffix(" Hz")
        self.target_fs_spin.setSpecialValueText("auto (fs minima del grupo)")
        self.target_fs_spin.valueChanged.connect(self._settings_edited)
        form.addRow("", self.enable_check)
        form.addRow("Corte bajo", self.low_spin)
        form.addRow("Corte alto", self.high_spin)
        form.addRow("Orden", self.order_spin)
        form.addRow("fs comun", self.target_fs_spin)
        left_layout.addWidget(box)

        hint = QLabel(
            "Como se combinan fs distintas: dentro de cada grupo (misma distancia) "
            "las capturas se resamplean a la fs comun (por defecto la minima del grupo, "
            "p. ej. 2929 Hz baja a 1020 Hz) y se alinean por su trigger. Las capturas "
            "viejas de 3 s aportan al promedio solo hasta donde llegan; la cola larga "
            "queda definida por las capturas de 10.59 s (NaN donde no hay dato, no se "
            "inventa señal). Ordenes 5-10 ya aplican bien (antes eran inestables con "
            "corte bajo cerca de 1 Hz); igual conviene orden bajo (2-4) si se ve "
            "el inicio de la señal deformado, por el mayor transitorio de un orden alto."
        )
        hint.setWordWrap(True)
        left_layout.addWidget(hint)

        self.preview_btn = QPushButton("Actualizar vista previa")
        self.preview_btn.clicked.connect(self.refresh_preview)
        left_layout.addWidget(self.preview_btn)
        left_layout.addStretch(1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 4, 8, 8)
        self.plot_widget = pg.GraphicsLayoutWidget()
        self.time_plot = self.plot_widget.addPlot(row=0, col=0, title="Geofono: original vs filtrada")
        self.time_plot.setLabel("bottom", "Tiempo relativo al hammer", units="s")
        self.time_plot.setLabel("left", "Geo", units="V")
        self._time_legend = self.time_plot.addLegend(offset=(10, 10))
        self.spec_plot = self.plot_widget.addPlot(row=1, col=0, title="Espectro (magnitud)")
        self.spec_plot.setLabel("bottom", "Frecuencia", units="Hz")
        self.spec_plot.setLabel("left", "|FFT|")
        self.spec_plot.setLogMode(x=True, y=True)
        self._spec_legend = self.spec_plot.addLegend(offset=(10, 10))
        right_layout.addWidget(self.plot_widget, stretch=1)
        self.status_label = QLabel(
            "Selecciona una captura en la pestaña Capturas y volve aca para previsualizar el filtro."
        )
        self.status_label.setWordWrap(True)
        right_layout.addWidget(self.status_label)

        root.addWidget(left)
        root.addWidget(right)
        root.setSizes([380, 900])
        self._load_settings_into_ui()
        self._style_plots()

    def _load_settings_into_ui(self) -> None:
        self._loading = True
        self.enable_check.setChecked(bool(self.settings.enabled))
        self.low_spin.setValue(float(self.settings.low_hz))
        self.high_spin.setValue(float(self.settings.high_hz))
        self.order_spin.setValue(int(self.settings.order))
        self.target_fs_spin.setValue(float(self.settings.target_fs))
        self._loading = False

    def _settings_edited(self, *_args) -> None:
        if self._loading:
            return
        self.settings.enabled = bool(self.enable_check.isChecked())
        self.settings.low_hz = float(self.low_spin.value())
        self.settings.high_hz = float(self.high_spin.value())
        self.settings.order = int(self.order_spin.value())
        self.settings.target_fs = float(self.target_fs_spin.value())
        if self.on_changed is not None:
            self.on_changed()
        self.refresh_preview()

    def refresh_preview(self) -> None:
        self.time_plot.clear()
        self.spec_plot.clear()
        self._time_legend.clear()
        self._spec_legend.clear()
        self._style_plots()
        data = self.get_current() if self.get_current is not None else None
        if data is None:
            self.status_label.setText(
                "Selecciona una captura en la pestaña Capturas y volve aca para previsualizar el filtro."
            )
            return
        fs = float(data["fs"])
        geo = np.asarray(data["geo"], dtype=np.float64)
        trigger_s = float(data["trigger_s"])
        # Misma cadena que usan los promedios: primero resamplear a la fs
        # comun (si esta fijada), despues filtrar.
        target_fs = float(self.settings.target_fs or 0.0)
        work_fs = fs
        work = geo
        if target_fs > 0 and abs(target_fs - fs) > 1e-6:
            work = np.asarray(resample_signal(geo, fs, target_fs), dtype=np.float64)
            work_fs = target_fs
        filtered = np.asarray(
            apply_bandpass_filter(
                work, work_fs, self.settings.low_hz, self.settings.high_hz, self.settings.order
            ),
            dtype=np.float64,
        )

        orig_color = (150, 150, 150, 160)
        filt_color = "#69b7ff" if self.dark_mode else "#0066cc"
        time_orig = np.arange(geo.size, dtype=np.float64) / fs - trigger_s
        time_filt = np.arange(filtered.size, dtype=np.float64) / work_fs - trigger_s
        self.time_plot.plot(time_orig, geo, pen=pg.mkPen(orig_color, width=1), name=f"original (fs {fs:g})")
        self.time_plot.plot(
            time_filt, filtered, pen=pg.mkPen(filt_color, width=1.6), name=f"filtrada (fs {work_fs:g})"
        )

        for signal_arr, sig_fs, color, name in (
            (geo, fs, orig_color, "original"),
            (filtered, work_fs, filt_color, "filtrada"),
        ):
            if signal_arr.size < 8:
                continue
            spec = np.abs(np.fft.rfft(signal_arr))
            freqs = np.fft.rfftfreq(signal_arr.size, d=1.0 / sig_fs)
            mask = freqs > 0
            self.spec_plot.plot(freqs[mask], np.maximum(spec[mask], 1e-12), pen=pg.mkPen(color, width=1.2), name=name)

        state = "ACTIVO en promedios/export" if self.settings.enabled else "solo vista previa (no aplicado)"
        resamp = f", resampleada {fs:g}->{work_fs:g} Hz" if abs(work_fs - fs) > 1e-6 else ""
        self.status_label.setText(f"{data['label']} | filtro {state}{resamp}")

    def set_dark_mode(self, dark: bool) -> None:
        self.dark_mode = bool(dark)
        self.refresh_preview()

    def _style_plots(self) -> None:
        bg = "#15181d" if self.dark_mode else "#ffffff"
        fg = "#eeeeee" if self.dark_mode else "#222222"
        self.plot_widget.setBackground(bg)
        for plot, title in (
            (self.time_plot, "Geofono: original vs filtrada"),
            (self.spec_plot, "Espectro (magnitud)"),
        ):
            plot.setTitle(title, color=fg)
            plot.showGrid(x=True, y=True, alpha=0.18 if self.dark_mode else 0.25)
            for axis_name in ("bottom", "left"):
                axis = plot.getAxis(axis_name)
                axis.setPen(pg.mkPen(fg))
                axis.setTextPen(pg.mkPen(fg))


_ALIGN_COLORS = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#46f0f0", "#f032e6", "#bcf60c", "#008080", "#9a6324"]


class AlignmentPanel(QWidget):
    """Tab de enfase: ajuste de offset de tiempo POR SEÑAL INDIVIDUAL (no por
    carpeta) para corregir errores de posicionamiento entre tandas medidas en
    dias/carpetas distintas antes de promediar.

    Navega las señales de un label en orden de pico a pico descendente
    (empezar por la mas facil de ver el golpe, mismo criterio que la tabla de
    Capturas). El grafico muestra el ACUMULADO de las señales ya confirmadas
    con "OK alineado" (en vez de todas a la vez), mas la señal actual
    resaltada, para ir armando el promedio de a una.

    El offset por señal tiene prioridad sobre el offset de carpeta (legado):
    una señal nunca ajustada a mano en este panel sigue usando el offset de
    su carpeta como default (ver `get_alignment_offset` en
    field_review_data.py). Offset positivo corre esa señal hacia la
    izquierda (llegada mas temprana)."""

    def __init__(
        self,
        dataset: FieldDataset,
        annotations: dict[str, PickAnnotation],
        offsets: dict[str, dict[str, float]],
        shot_offsets: dict[str, float],
        get_zeroed,
        get_peak_to_peak,
        dark_mode: bool = False,
        on_changed=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.dataset = dataset
        self.annotations = annotations
        self.offsets = offsets
        self.shot_offsets = shot_offsets
        self.get_zeroed = get_zeroed
        self.get_peak_to_peak = get_peak_to_peak
        self.dark_mode = dark_mode
        self.on_changed = on_changed
        self._loading = False
        self._shots: list[tuple[FieldShot, PickAnnotation]] = []
        self._index = 0
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        root = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(root)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        pick_box = QHBoxLayout()
        pick_box.addWidget(QLabel("Label"))
        self.label_combo = QComboBox()
        self.label_combo.currentIndexChanged.connect(self._label_changed)
        pick_box.addWidget(self.label_combo, stretch=1)
        left_layout.addLayout(pick_box)

        self.shot_label = QLabel("-")
        self.shot_label.setWordWrap(True)
        left_layout.addWidget(self.shot_label)

        nav_box = QHBoxLayout()
        self.prev_btn = QPushButton("Anterior")
        self.prev_btn.clicked.connect(lambda: self._move(-1))
        self.next_btn = QPushButton("Siguiente")
        self.next_btn.clicked.connect(lambda: self._move(1))
        nav_box.addWidget(self.prev_btn)
        nav_box.addWidget(self.next_btn)
        left_layout.addLayout(nav_box)

        form_box = QGroupBox("Offset de esta señal (micro-ajuste)")
        form = QFormLayout(form_box)
        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setRange(-500.0, 500.0)
        self.offset_spin.setDecimals(3)
        self.offset_spin.setSingleStep(0.5)
        self.offset_spin.setSuffix(" ms")
        self.offset_spin.valueChanged.connect(self._offset_changed)
        form.addRow("Offset", self.offset_spin)
        left_layout.addWidget(form_box)

        self.ok_btn = QPushButton("OK alineado (guarda y pasa a la siguiente)")
        self.ok_btn.clicked.connect(self._mark_ok)
        left_layout.addWidget(self.ok_btn)

        reset_box = QHBoxLayout()
        self.reset_shot_btn = QPushButton("Reset esta señal")
        self.reset_shot_btn.clicked.connect(self._reset_shot)
        self.reset_label_btn = QPushButton("Reset todo el label")
        self.reset_label_btn.clicked.connect(self._reset_label)
        reset_box.addWidget(self.reset_shot_btn)
        reset_box.addWidget(self.reset_label_btn)
        left_layout.addLayout(reset_box)

        hint = QLabel(
            "Orden: mayor pico a pico primero (la mas facil de ver el golpe). "
            "El offset es por señal individual, no por carpeta; una señal "
            "nunca ajustada usa el offset de su carpeta como default. El "
            "grafico muestra el acumulado de las ya confirmadas con 'OK "
            "alineado' (un color por señal) mas la señal actual resaltada. "
            "Se aplica a promedios, waterfall, MASW y export."
        )
        hint.setWordWrap(True)
        left_layout.addWidget(hint)
        left_layout.addStretch(1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 4, 8, 8)
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Tiempo relativo al hammer", units="s")
        self.plot.setLabel("left", "Geo", units="V")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        right_layout.addWidget(self.plot, stretch=1)
        self.status_label = QLabel("Elegi un label para empezar a alinear")
        self.status_label.setWordWrap(True)
        right_layout.addWidget(self.status_label)

        root.addWidget(left)
        root.addWidget(right)
        root.setSizes([380, 900])
        self._apply_theme()

    def refresh(self) -> None:
        """Rearma la lista de labels desde las marcas actuales, conservando la
        seleccion si se puede."""
        current = self.label_combo.currentText()
        labels = sorted(
            {
                format_distance_label(ann.distance_m)
                for shot in self.dataset.shots
                for ann in (self.annotations.get(shot.shot_id),)
                if ann is not None and ann.accepted
            }
        )
        self._loading = True
        self.label_combo.clear()
        self.label_combo.addItems(labels)
        if current in labels:
            self.label_combo.setCurrentText(current)
        self._loading = False
        self._label_changed()

    def _ordered_shots_for_label(self, label: str) -> list[tuple[FieldShot, PickAnnotation]]:
        """Señales aceptadas de ese label, ordenadas por pico a pico
        descendente (igual criterio que Capturas): se empieza por la mas
        facil de ver para guiar el ajuste del resto."""
        matches: list[tuple[FieldShot, PickAnnotation]] = []
        for shot in self.dataset.shots:
            ann = self.annotations.get(shot.shot_id)
            if ann is None or not ann.accepted:
                continue
            if format_distance_label(ann.distance_m) != label:
                continue
            matches.append((shot, ann))
        matches.sort(key=lambda pair: -self.get_peak_to_peak(pair[0]))
        return matches

    def _is_accumulated(self, shot: FieldShot) -> bool:
        return shot.shot_id in self.shot_offsets

    def _shot_default_offset_s(self, shot: FieldShot, ann: PickAnnotation) -> float:
        if shot.shot_id in self.shot_offsets:
            return float(self.shot_offsets[shot.shot_id])
        label = format_distance_label(ann.distance_m)
        return float(self.offsets.get(label, {}).get(shot.folder_name, 0.0))

    def _current(self) -> tuple[FieldShot, PickAnnotation] | None:
        if not (0 <= self._index < len(self._shots)):
            return None
        return self._shots[self._index]

    def _label_changed(self, *_args) -> None:
        if self._loading:
            return
        label = self.label_combo.currentText()
        self._shots = self._ordered_shots_for_label(label) if label else []
        self._index = 0
        self._show_current()

    def _show_current(self) -> None:
        current = self._current()
        self._loading = True
        if current is None:
            self.shot_label.setText("-")
            self.offset_spin.setValue(0.0)
        else:
            shot, ann = current
            n_done = sum(1 for s, _a in self._shots if self._is_accumulated(s))
            estado = "ya alineada" if self._is_accumulated(shot) else "sin alinear todavia"
            self.shot_label.setText(
                f"{self._index + 1}/{len(self._shots)} — {shot.folder_name}/{shot.capture_name}\n"
                f"dist {ann.distance_m:.3f} m — {estado} — {n_done}/{len(self._shots)} acumuladas"
            )
            self.offset_spin.setValue(self._shot_default_offset_s(shot, ann) * 1000.0)
        self._loading = False
        self._redraw()

    def _move(self, delta: int) -> None:
        if not self._shots:
            return
        self._index = int(np.clip(self._index + delta, 0, len(self._shots) - 1))
        self._show_current()

    def _offset_changed(self, _value_ms: float) -> None:
        if self._loading:
            return
        self._redraw()

    def _mark_ok(self) -> None:
        current = self._current()
        if current is None:
            return
        shot, _ann = current
        self.shot_offsets[shot.shot_id] = float(self.offset_spin.value()) / 1000.0
        if self.on_changed is not None:
            self.on_changed()
        self._move(1)

    def _reset_shot(self) -> None:
        current = self._current()
        if current is None:
            return
        shot, ann = current
        self.shot_offsets.pop(shot.shot_id, None)
        if self.on_changed is not None:
            self.on_changed()
        self._loading = True
        self.offset_spin.setValue(self._shot_default_offset_s(shot, ann) * 1000.0)
        self._loading = False
        self._show_current()

    def _reset_label(self) -> None:
        label = self.label_combo.currentText()
        if label in self.offsets:
            self.offsets[label] = {}
        for shot, _ann in self._shots:
            self.shot_offsets.pop(shot.shot_id, None)
        if self.on_changed is not None:
            self.on_changed()
        self._show_current()

    _MEAN_COLOR = "#2ca02c"

    def _current_highlight_color(self) -> str:
        return "#ffffff" if self.dark_mode else "#000000"

    def _partial_mean(self, exclude_shot_id: str | None) -> tuple[np.ndarray, np.ndarray, int] | None:
        """Media parcial de las señales YA alineadas (acumuladas) del label
        actual, cada una corrida por su trigger + su offset guardado. Devuelve
        (tiempo, media, n) o None si todavía no hay ninguna acumulada.

        Se interpola cada señal a una grilla de tiempo común (maneja fs
        mezcladas 2929/1020 sin problema); NaN fuera del tramo de cada una,
        nanmean por columna. No dibuja las señales individuales: el usuario
        solo ve esta media como referencia para alinear la señal actual."""
        traces: list[tuple[np.ndarray, np.ndarray]] = []
        for shot, ann in self._shots:
            if shot.shot_id == exclude_shot_id or not self._is_accumulated(shot):
                continue
            fs = float(shot.fs or shot.geo.fs or shot.hammer.fs)
            if fs <= 0:
                continue
            try:
                _hammer, geo = self.get_zeroed(shot, ann)
            except Exception:
                continue
            if geo.size == 0:
                continue
            offset_s = float(self.shot_offsets.get(shot.shot_id, 0.0))
            t = np.arange(geo.size, dtype=np.float64) / fs - float(ann.trigger_s) - offset_s
            traces.append((t, geo.astype(np.float64)))
        if not traces:
            return None
        t_min = max(min(float(t[0]) for t, _g in traces), -0.2)
        t_max = min(max(float(t[-1]) for t, _g in traces), 2.0)
        if t_max <= t_min:
            return None
        dt = min((float(t[1] - t[0]) for t, _g in traces if t.size > 1), default=1e-3)
        if dt <= 0:
            return None
        n = min(int((t_max - t_min) / dt) + 1, 20000)
        grid = np.linspace(t_min, t_max, max(n, 2))
        stack = [np.interp(grid, t, g, left=np.nan, right=np.nan) for t, g in traces]
        with np.errstate(invalid="ignore"):
            mean = np.nanmean(np.vstack(stack), axis=0)
        return grid, mean, len(traces)

    def _redraw(self) -> None:
        self.plot.clear()
        label = self.label_combo.currentText()
        current = self._current()
        current_shot_id = current[0].shot_id if current is not None else None
        # Media parcial de las ya alineadas (una sola traza de referencia, no
        # las 30 individuales) — excluye la señal actual.
        mean_result = self._partial_mean(exclude_shot_id=current_shot_id)
        n_acc = 0
        if mean_result is not None:
            grid, mean, n_acc = mean_result
            self.plot.plot(
                grid, mean, pen=pg.mkPen(self._MEAN_COLOR, width=3), name="media parcial"
            )
        # Señal actual (con el offset en vivo del spin) para moverla contra la media.
        if current is not None:
            shot, ann = current
            fs = float(shot.fs or shot.geo.fs or shot.hammer.fs)
            if fs > 0:
                try:
                    _hammer, geo = self.get_zeroed(shot, ann)
                except Exception:
                    geo = np.array([])
                if geo.size:
                    offset_s = float(self.offset_spin.value()) / 1000.0
                    time = np.arange(geo.size, dtype=np.float64) / fs - float(ann.trigger_s) - offset_s
                    self.plot.plot(
                        time, geo, pen=pg.mkPen(self._current_highlight_color(), width=2), name="señal actual"
                    )
        self.plot.setXRange(-0.1, 1.0, padding=0.02)
        if not label:
            self.status_label.setText("Elegi un label para empezar a alinear")
        else:
            ref = "verde" if n_acc else "todavía sin media (esta será la primera)"
            self.status_label.setText(
                f"{label}: media parcial de {n_acc} ya alineadas ({ref}) + señal actual "
                f"({'blanco' if self.dark_mode else 'negro'}). Mové el offset para calzarla."
            )

    def set_dark_mode(self, dark: bool) -> None:
        self.dark_mode = bool(dark)
        self._apply_theme()
        self._show_current()

    def _apply_theme(self) -> None:
        bg = "#15181d" if self.dark_mode else "#ffffff"
        fg = "#eeeeee" if self.dark_mode else "#222222"
        self.plot.setBackground(bg)
        for axis_name in ("bottom", "left"):
            axis = self.plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(fg))
            axis.setTextPen(pg.mkPen(fg))


class AverageReviewPanel(QWidget):
    def __init__(
        self,
        dataset: FieldDataset,
        annotations: dict[str, PickAnnotation],
        output_dir: str | Path,
        prefer_filtered: bool = False,
        dark_mode: bool = False,
        on_show_waterfall=None,
        filter_settings: FilterSettings | None = None,
        alignment_offsets: dict[str, dict[str, float]] | None = None,
        alignment_shot_offsets: dict[str, float] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.dataset = dataset
        self.annotations = annotations
        self.output_dir = Path(output_dir)
        self.prefer_filtered = prefer_filtered
        self.dark_mode = dark_mode
        self.on_show_waterfall = on_show_waterfall
        self.filter_settings = filter_settings
        self.alignment_offsets = alignment_offsets
        self.alignment_shot_offsets = alignment_shot_offsets
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
        signature = (
            annotations_signature(self.dataset, self.annotations),
            filter_settings_signature(self.filter_settings),
            alignment_offsets_signature(self.alignment_offsets),
            alignment_shot_offsets_signature(self.alignment_shot_offsets),
        )
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
                filter_settings=self.filter_settings,
                alignment_offsets=self.alignment_offsets,
                alignment_shot_offsets=self.alignment_shot_offsets,
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
        filt = self.filter_settings
        filter_txt = ""
        if filt is not None and filt.enabled:
            filter_txt = f" | filtro {filt.low_hz:g}-{filt.high_hz:g} Hz o{filt.order}"
        self.summary_label.setText(f"{len(self.averages)} promedios{filter_txt} | {self.output_dir}")
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
                filter_settings=self.filter_settings,
                alignment_offsets=self.alignment_offsets,
                alignment_shot_offsets=self.alignment_shot_offsets,
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
    AverageReviewPanel via `populate()`, no calcula nada por si mismo.

    Permite recortar el rango de tiempo mostrado y tildar/destildar trazas
    por distancia para inspeccionar sin ruido visual. Este recorte/filtro es
    SOLO de vista (y de lo que se manda a MASW con 'Ver MASW'/'Auto
    inversion'); no afecta el CSV/PNG/PDF exportados desde la pestaña
    Promedios, que siguen usando todas las distancias y el rango completo
    (ver nota en HANDOFF_FIELD_REVIEW.md, Fase 8)."""

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
        self._hidden_distances: set[float] = set()
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
        self.kfilter_check = QCheckBox("Filtro K (quitar rebotes)")
        self.kfilter_check.setToolTip(
            "Filtro direccional frecuencia-numero de onda (f-k).\n"
            "Separa las ondas por direccion y deja solo las que viajan de la fuente hacia\n"
            "los geofonos (moveout positivo), eliminando rebotes/reflexiones que vuelven.\n"
            "Se aplica a la vista y a lo que se manda a MASW; el export no se toca."
        )
        self.kfilter_check.toggled.connect(self._redraw)
        top.addWidget(self.kfilter_check)
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

        trim_box = QHBoxLayout()
        self.trim_enabled_check = QCheckBox("Recortar tiempo")
        self.trim_enabled_check.setToolTip(
            "Solo afecta la vista y lo que se manda a MASW; el export de Promedios sigue "
            "usando el rango completo."
        )
        self.trim_enabled_check.toggled.connect(self._redraw)
        self.trim_start_spin = QDoubleSpinBox()
        self.trim_start_spin.setRange(-100.0, 100.0)
        self.trim_start_spin.setDecimals(4)
        self.trim_start_spin.setSingleStep(0.01)
        self.trim_start_spin.setSuffix(" s")
        self.trim_start_spin.valueChanged.connect(self._redraw)
        self.trim_end_spin = QDoubleSpinBox()
        self.trim_end_spin.setRange(-100.0, 100.0)
        self.trim_end_spin.setDecimals(4)
        self.trim_end_spin.setSingleStep(0.01)
        self.trim_end_spin.setSuffix(" s")
        self.trim_end_spin.setValue(1.0)
        self.trim_end_spin.valueChanged.connect(self._redraw)
        trim_box.addWidget(self.trim_enabled_check)
        trim_box.addWidget(QLabel("desde"))
        trim_box.addWidget(self.trim_start_spin)
        trim_box.addWidget(QLabel("hasta"))
        trim_box.addWidget(self.trim_end_spin)
        trim_box.addStretch(1)
        layout.addLayout(trim_box)

        body = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(body, stretch=1)

        traces_box = QWidget()
        traces_layout = QVBoxLayout(traces_box)
        traces_layout.setContentsMargins(0, 0, 4, 0)
        traces_layout.addWidget(QLabel("Trazas (distancia)"))
        self.trace_list = QListWidget()
        self.trace_list.itemChanged.connect(self._trace_item_changed)
        traces_layout.addWidget(self.trace_list, stretch=1)
        trace_btn_box = QHBoxLayout()
        self.trace_all_btn = QPushButton("Todas")
        self.trace_all_btn.clicked.connect(lambda: self._set_all_traces(True))
        self.trace_none_btn = QPushButton("Ninguna")
        self.trace_none_btn.clicked.connect(lambda: self._set_all_traces(False))
        trace_btn_box.addWidget(self.trace_all_btn)
        trace_btn_box.addWidget(self.trace_none_btn)
        traces_layout.addLayout(trace_btn_box)
        body.addWidget(traces_box)

        plot_box = QWidget()
        plot_layout = QVBoxLayout(plot_box)
        plot_layout.setContentsMargins(4, 0, 0, 0)
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Tiempo relativo al hammer", units="s")
        self.plot.setLabel("left", "Distancia [m] + amplitud normalizada")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        plot_layout.addWidget(self.plot, stretch=1)
        self.coord_label = QLabel("Move el mouse sobre el grafico para ver tiempo/distancia bajo el cursor")
        plot_layout.addWidget(self.coord_label)
        body.addWidget(plot_box)
        body.setSizes([180, 900])

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

    def _trace_item_changed(self, _item: QListWidgetItem) -> None:
        self._hidden_distances = {
            self.trace_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.trace_list.count())
            if self.trace_list.item(i).checkState() == Qt.CheckState.Unchecked
        }
        self._redraw()

    def _set_all_traces(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.trace_list.blockSignals(True)
        for i in range(self.trace_list.count()):
            self.trace_list.item(i).setCheckState(state)
        self.trace_list.blockSignals(False)
        self._trace_item_changed(None)

    def _rebuild_trace_list(self, distances: list[float]) -> None:
        self.trace_list.blockSignals(True)
        self.trace_list.clear()
        for distance in distances:
            key = round(float(distance), 6)
            item = QListWidgetItem(format_distance_label(distance))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setCheckState(
                Qt.CheckState.Unchecked if key in self._hidden_distances else Qt.CheckState.Checked
            )
            self.trace_list.addItem(item)
        self.trace_list.blockSignals(False)
        # Distancias que ya no estan en este dataset no tiene sentido seguir
        # recordandolas como ocultas.
        valid_keys = {round(float(d), 6) for d in distances}
        self._hidden_distances &= valid_keys

    def _trimmed_time_and_matrix(
        self, common_time: np.ndarray, matrix: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        if not self.trim_enabled_check.isChecked() or common_time.size == 0:
            return common_time, matrix
        start_s = float(self.trim_start_spin.value())
        end_s = float(self.trim_end_spin.value())
        if end_s <= start_s:
            return common_time, matrix
        lo = int(np.searchsorted(common_time, start_s, side="left"))
        hi = int(np.searchsorted(common_time, end_s, side="right"))
        lo = max(0, min(lo, common_time.size - 1))
        hi = max(lo + 1, min(hi, common_time.size))
        return common_time[lo:hi], matrix[:, lo:hi]

    def _apply_kfilter(self, common_time: np.ndarray, matrix: np.ndarray) -> np.ndarray:
        """Filtro f-k direccional (quita rebotes) sobre TODAS las distancias,
        antes de descartar las ocultas — asi la resolucion en k usa el tendido
        completo. Se aplica sobre la matriz ya recortada en tiempo."""
        if not self.kfilter_check.isChecked() or self._last_data is None:
            return matrix
        distances = self._last_data["distances"]
        try:
            return np.asarray(fk_directional_filter(matrix, distances, common_time), dtype=np.float64)
        except Exception:
            return matrix

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
        self._rebuild_trace_list(distances)
        self._redraw()

    # -------------------------------------------------- persistencia (feat 3)

    def get_state(self) -> dict:
        d = self._last_data
        hammer = d.get("hammer_global") if d else None
        return {
            "hidden_distances": [float(v) for v in sorted(self._hidden_distances)],
            "trim_enabled": bool(self.trim_enabled_check.isChecked()),
            "trim_start": float(self.trim_start_spin.value()),
            "trim_end": float(self.trim_end_spin.value()),
            "raw_amplitude": bool(self.raw_amplitude_check.isChecked()),
            "kfilter": bool(self.kfilter_check.isChecked()),
            "n_averages": int(d["n_averages"]) if d else 0,
            "has_data": d is not None,
            "hammer_n": (hammer.get("n") if isinstance(hammer, dict) else None),
            "hammer_inverted": (bool(hammer.get("inverted")) if isinstance(hammer, dict) else None),
        }

    def get_arrays(self) -> dict:
        d = self._last_data
        if d is None:
            return {}
        arrays = {
            "wf_common_time": np.asarray(d["common_time"], dtype=np.float64),
            "wf_distances": np.asarray(d["distances"], dtype=np.float64),
            "wf_matrix": np.asarray(d["matrix"], dtype=np.float64),
        }
        hammer = d.get("hammer_global")
        if isinstance(hammer, dict):
            try:
                ht, hm = hammer_global_time_signal(hammer)
                arrays["wf_hammer_time"] = np.asarray(ht, dtype=np.float64)
                arrays["wf_hammer_mean"] = np.asarray(hm, dtype=np.float64)
            except Exception:
                pass
        return arrays

    def restore_state(
        self,
        state: dict,
        arrays: dict | None,
        arrivals: dict[str, AverageArrivalAnnotation] | None,
    ) -> None:
        if not state:
            return
        arrays = arrays or {}
        # Los ajustes de vista se ponen ANTES de populate (block signals para no
        # redibujar a mitad); _rebuild_trace_list/_redraw ya los usan.
        self._hidden_distances = {round(float(v), 6) for v in state.get("hidden_distances", [])}
        for widget, key in (
            (self.trim_enabled_check, "trim_enabled"),
            (self.raw_amplitude_check, "raw_amplitude"),
            (self.kfilter_check, "kfilter"),
        ):
            widget.blockSignals(True)
            widget.setChecked(bool(state.get(key, False)))
            widget.blockSignals(False)
        for spin, key in ((self.trim_start_spin, "trim_start"), (self.trim_end_spin, "trim_end")):
            if key in state:
                spin.blockSignals(True)
                spin.setValue(float(state[key]))
                spin.blockSignals(False)
        if "wf_matrix" not in arrays:
            return
        hammer_global = None
        if "wf_hammer_time" in arrays and "wf_hammer_mean" in arrays:
            hammer_global = {
                "time_s": np.asarray(arrays["wf_hammer_time"], dtype=np.float64),
                "hammer_mean_v": np.asarray(arrays["wf_hammer_mean"], dtype=np.float64),
                "n": state.get("hammer_n"),
                "inverted": bool(state.get("hammer_inverted")),
            }
        self.populate(
            np.asarray(arrays["wf_common_time"], dtype=np.float64),
            [float(d) for d in arrays["wf_distances"]],
            np.asarray(arrays["wf_matrix"], dtype=np.float64),
            arrivals,
            hammer_global,
            int(state.get("n_averages", 0)),
        )

    def _visible_distances_and_matrix(
        self, distances: list[float], matrix: np.ndarray
    ) -> tuple[list[float], np.ndarray]:
        if not self._hidden_distances:
            return distances, matrix
        keep = [i for i, d in enumerate(distances) if round(float(d), 6) not in self._hidden_distances]
        if not keep:
            return [], matrix[:0]
        return [distances[i] for i in keep], matrix[keep, :]

    def _redraw(self) -> None:
        if self._last_data is None:
            return
        common_time, matrix = self._trimmed_time_and_matrix(
            self._last_data["common_time"], self._last_data["matrix"]
        )
        matrix = self._apply_kfilter(common_time, matrix)
        distances, matrix = self._visible_distances_and_matrix(self._last_data["distances"], matrix)
        arrivals = self._last_data["arrivals"]
        hammer_global = self._last_data["hammer_global"]
        n_averages = self._last_data["n_averages"]
        raw_amplitude = self.raw_amplitude_check.isChecked()

        self.plot.clear()
        self.plot.addItem(self._crosshair_v, ignoreBounds=True)
        self.plot.addItem(self._crosshair_h, ignoreBounds=True)
        self._crosshair_v.hide()
        self._crosshair_h.hide()

        if common_time.size == 0 or not distances:
            self.info_label.setText(
                f"{n_averages} promedios | sin trazas visibles (recorte o filtro vacio)"
            )
            return

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
        common_time, matrix = self._trimmed_time_and_matrix(
            self._last_data["common_time"], self._last_data["matrix"]
        )
        matrix = self._apply_kfilter(common_time, matrix)
        distances, matrix = self._visible_distances_and_matrix(self._last_data["distances"], matrix)
        if common_time.size == 0 or not distances:
            QMessageBox.warning(
                self, "MASW", "No hay trazas visibles con el recorte/filtro actual de la pestaña Waterfall."
            )
            return
        callback(common_time, distances, matrix)


class MaswPanel(QWidget):
    """Tab de analisis MASW con las tres etapas del flujo clasico:

    1. Dispersion: imagen frecuencia-velocidad (phase-shift, Park et al.
       1998) y picking de la curva de dispersion (auto + manual con click).
    2. Inversion: busqueda Monte Carlo del perfil de capas que mejor
       reproduce la curva picada (fast delta matrix como forward model).
    3. Perfil Vs: perfil de velocidad de corte vs profundidad resultante.

    Algoritmos en masw_dispersion.py / masw_inversion.py (puertos a numpy
    de third-party/maswavespy, sin depender de compilar Cython).

    Multi-modo: se pueden definir N regiones (una por modo, M0 fundamental,
    M1 primer modo superior, ...) y el auto-pick arma una curva de dispersion
    experimental por modo. La inversion corre sobre el modo activo (selector);
    si esta instalado evodcinv/disba puede hacer inversion conjunta multimodo."""

    # Colores por modo (M0 fundamental, M1, M2, ...). Se cicla si hay mas.
    _MODE_COLORS = ["#ff3b30", "#34c759", "#0a84ff", "#ff9f0a", "#bf5af2", "#ffd60a"]

    @classmethod
    def _mode_color(cls, mode: int) -> str:
        return cls._MODE_COLORS[int(mode) % len(cls._MODE_COLORS)]

    def __init__(self, dark_mode: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.dark_mode = dark_mode
        self._raw_time: np.ndarray | None = None
        self._raw_distances: list[float] | None = None
        self._raw_matrix: np.ndarray | None = None
        self._last_result: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None
        # Picks por modo: modo 0 = fundamental, 1 = primer modo superior, etc.
        # `self.picks` es un alias (misma identidad de dict) al modo activo, que
        # es el que se edita y el que va a la inversion. Todas las mutaciones
        # in-place (self.picks[k]=..., del, .clear()) se reflejan en el modo.
        self.picks_by_mode: dict[int, dict[float, float]] = {0: {}}
        self._active_mode = 0
        self.picks: dict[float, float] = self.picks_by_mode[0]
        self._inv_result: dict | None = None
        self._mm_result: dict | None = None  # ultimo resultado de inversion multimodo
        self._abort_inversion = False
        self._inverting = False
        self._pick_mode = "ver"
        self._dragging_pick: float | None = None
        self._default_drag_event = None
        self._geophone_spacing_m: float | None = None
        # Largo del arreglo L (span de distancias): fija la longitud de onda
        # maxima confiable lambda_max = L, o sea c <= L*f (techo del picking,
        # complementario al piso anti-aliasing c >= 2*dx*f).
        self._array_length_m: float | None = None
        # Regiones por modo: `_regions_by_mode[m]` es la LISTA de poligonos
        # (cada uno lista de vertices (f, c)) que delimitan el modo m para el
        # auto-pick. Un modo puede tener varias regiones. La region que se
        # dibuja se agrega al MODO ACTIVO. `_m0_draft` son los vertices que se
        # van clickeando del poligono en construccion (`_m0_drawing` True).
        self._regions_by_mode: dict[int, list[list[tuple[float, float]]]] = {0: []}
        self._m0_draft: list[tuple[float, float]] = []
        self._m0_drawing = False
        self._m0_line: pg.PlotDataItem | None = None
        self._m0_vertices: pg.ScatterPlotItem | None = None
        self._region_labels: list[pg.TextItem] = []
        self._alias_line: pg.PlotDataItem | None = None
        self._lambda_max_line: pg.PlotDataItem | None = None
        # Edicion de la curva en la pestaña '2. Inversion' (arrastrar/borrar).
        self._inv_edit_mode = "mover"
        self._inv_drag_key: float | None = None
        self._inv_drag_pos: tuple[float, float] | None = None
        self._inv_default_drag_event = None
        self._inv_model_items: list = []
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
        self.clear_picks_btn = QPushButton("Limpiar picks")
        self.clear_picks_btn.clicked.connect(self._clear_picks)
        self.to_inversion_btn = QPushButton("Usar curva → Inversion")
        self.to_inversion_btn.clicked.connect(self._go_to_inversion)
        pick_row.addWidget(QLabel("Picking: f desde"))
        pick_row.addWidget(self.pick_fmin_spin)
        pick_row.addWidget(QLabel("hasta"))
        pick_row.addWidget(self.pick_fmax_spin)
        pick_row.addWidget(self.autopick_btn)
        pick_row.addWidget(self.clear_picks_btn)
        pick_row.addWidget(self.to_inversion_btn)
        pick_row.addStretch(1)
        layout.addLayout(pick_row)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Click en la imagen:"))
        self.pick_mode_group = QButtonGroup(self)
        self.pick_mode_group.setExclusive(True)
        self.mode_ver_btn = QPushButton("Ver (sin editar)")
        self.mode_add_btn = QPushButton("Añadir")
        self.mode_add_btn.setToolTip("Click agrega un pick nuevo en el bin de frecuencia mas cercano al click.")
        self.mode_delete_btn = QPushButton("Borrar")
        self.mode_delete_btn.setToolTip("Click borra el pick existente mas cercano al click.")
        self.mode_drag_btn = QPushButton("Arrastrar")
        self.mode_drag_btn.setToolTip(
            "Click y arrastre sobre un pick existente para mover su velocidad "
            "(la frecuencia del pick no cambia al arrastrar)."
        )
        for btn, mode in (
            (self.mode_ver_btn, "ver"),
            (self.mode_add_btn, "anadir"),
            (self.mode_delete_btn, "borrar"),
            (self.mode_drag_btn, "arrastrar"),
        ):
            btn.setCheckable(True)
            self.pick_mode_group.addButton(btn)
            btn.clicked.connect(lambda _checked, m=mode: self._set_pick_mode(m))
            mode_row.addWidget(btn)
        self.mode_ver_btn.setChecked(True)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        m0_row = QHBoxLayout()
        self.add_mode_btn = QPushButton("+ Agregar modo")
        self.add_mode_btn.setToolTip(
            "Crea un modo nuevo (M0 → M1 → ...) y lo deja activo. Despues defini sus regiones "
            "y pickea; cambia entre modos con 'Modo activo'."
        )
        self.add_mode_btn.clicked.connect(self._add_mode)
        self.m0_start_btn = QPushButton("Iniciar región")
        self.m0_start_btn.setToolTip(
            "Empieza a dibujar una region (poligono) para el MODO ACTIVO: click en la imagen "
            "para ir agregando vertices, punto a punto. Un modo puede tener varias regiones."
        )
        self.m0_start_btn.clicked.connect(self._start_m0_polygon)
        self.m0_close_btn = QPushButton("Cerrar región")
        self.m0_close_btn.setToolTip(
            "Cierra el poligono uniendo el ultimo vertice con el primero y lo agrega al modo activo."
        )
        self.m0_close_btn.clicked.connect(self._close_m0_polygon)
        self.m0_close_btn.setEnabled(False)
        self.clear_m0_region_btn = QPushButton("Quitar regiones del modo")
        self.clear_m0_region_btn.setToolTip("Borra las regiones del modo activo (los picks quedan).")
        self.clear_m0_region_btn.clicked.connect(self._clear_m0_region)
        m0_row.addWidget(self.add_mode_btn)
        m0_row.addWidget(self.m0_start_btn)
        m0_row.addWidget(self.m0_close_btn)
        m0_row.addWidget(self.clear_m0_region_btn)
        m0_row.addWidget(QLabel("Modo activo"))
        self.active_mode_combo = QComboBox()
        self.active_mode_combo.setToolTip(
            "Modo que se edita (Añadir/Borrar/Arrastrar, dibujo de regiones y la pestaña de "
            "inversion). Cambia entre los modos creados."
        )
        self.active_mode_combo.currentIndexChanged.connect(self._active_mode_changed)
        m0_row.addWidget(self.active_mode_combo)
        self.mode_legend_label = QLabel("")
        self.mode_legend_label.setTextFormat(Qt.TextFormat.RichText)
        self.mode_legend_label.setToolTip("Color de cada modo (picks y regiones).")
        m0_row.addWidget(self.mode_legend_label)
        m0_row.addStretch(1)
        layout.addLayout(m0_row)

        self.alias_info_label = QLabel("")
        self.alias_info_label.setWordWrap(True)
        layout.addWidget(self.alias_info_label)

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
        self._m0_line = self._plot_item.plot([], [], pen=pg.mkPen("#ffcc00", width=2))
        self._m0_line.setZValue(45)
        self._m0_vertices = pg.ScatterPlotItem(
            size=8, brush=pg.mkBrush("#ffcc00"), pen=pg.mkPen("#000000", width=1)
        )
        self._m0_vertices.setZValue(46)
        self._plot_item.addItem(self._m0_vertices)
        layout.addWidget(self.image_view, stretch=1)
        self.coord_label = QLabel("Move el mouse sobre la imagen para ver frecuencia / velocidad / amplitud")
        layout.addWidget(self.coord_label)
        self._plot_item.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._plot_item.scene().sigMouseClicked.connect(self._on_mouse_clicked)
        self._default_drag_event = self._plot_item.vb.mouseDragEvent
        self._plot_item.vb.mouseDragEvent = self._vb_mouse_drag_event
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

        self.run_mm_btn = QPushButton("Inversión conjunta multimodo")
        self.run_mm_btn.setToolTip(
            "Ajusta un unico perfil de capas a TODAS las curvas de modo a la vez (evodcinv + disba, "
            "algoritmo evolutivo CPSO). Necesita al menos un modo con 3+ picks.\n"
            "Requiere: pip install disba evodcinv."
        )
        self.run_mm_btn.clicked.connect(self._run_multimodal_inversion)
        left_layout.addWidget(self.run_mm_btn)

        edit_box = QGroupBox("Editar curva (puntos)")
        edit_layout = QVBoxLayout(edit_box)
        edit_hint = QLabel(
            "Sobre la curva de dispersion de abajo podes retocar los picks antes de invertir:"
        )
        edit_hint.setWordWrap(True)
        edit_layout.addWidget(edit_hint)
        edit_mode_row = QHBoxLayout()
        self.inv_edit_group = QButtonGroup(self)
        self.inv_edit_group.setExclusive(True)
        self.inv_move_btn = QPushButton("Mover")
        self.inv_move_btn.setToolTip("Arrastra un punto para moverlo libremente en frecuencia y velocidad.")
        self.inv_delete_btn = QPushButton("Borrar")
        self.inv_delete_btn.setToolTip("Click sobre un punto para borrarlo de la curva.")
        for btn, mode in ((self.inv_move_btn, "mover"), (self.inv_delete_btn, "borrar")):
            btn.setCheckable(True)
            self.inv_edit_group.addButton(btn)
            btn.clicked.connect(lambda _checked, m=mode: self._set_inv_edit_mode(m))
            edit_mode_row.addWidget(btn)
        self.inv_move_btn.setChecked(True)
        edit_layout.addLayout(edit_mode_row)
        left_layout.addWidget(edit_box)

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
        # Item persistente con la curva experimental (picks). Es editable:
        # arrastrar mueve un punto (feat: libremente en f y c), click en modo
        # 'Borrar' lo saca. Se mantiene entre corridas de inversion asi los
        # modelos teoricos se dibujan encima sin borrarlo.
        self._inv_observed_item = self.inv_plot.plot(
            [], [], pen=None, symbol="o", symbolSize=8,
            symbolBrush="#ff3b30", symbolPen=pg.mkPen("#ffffff", width=1),
            name="Curva experimental (picks)",
        )
        self._inv_observed_item.setZValue(60)
        self._inv_default_drag_event = self.inv_plot.getPlotItem().vb.mouseDragEvent
        self.inv_plot.getPlotItem().vb.mouseDragEvent = self._inv_vb_mouse_drag_event
        self.inv_plot.scene().sigMouseClicked.connect(self._on_inv_mouse_clicked)
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
        self._geophone_spacing_m = self._estimate_spacing_m(distances)
        self._array_length_m = self._estimate_array_length(distances)
        self._reset_all_modes()
        self.inner_tabs.setCurrentIndex(0)
        spacing_txt = f"{self._geophone_spacing_m:.2f} m" if self._geophone_spacing_m else "desconocido"
        length_txt = f"{self._array_length_m:.1f} m" if self._array_length_m else "desconocido"
        self.info_label.setText(
            f"Datos listos: {len(distances)} canales (distancias), ventana comun 0-{t_trim[-1]:.3f} s, "
            f"espaciado geofonos ~{spacing_txt}, largo del arreglo L~{length_txt}. "
            "Ajusta los parametros y presiona 'Calcular imagen'."
        )

    @staticmethod
    def _estimate_spacing_m(distances: list[float]) -> float | None:
        """Espaciado tipico entre geofonos (mediana de las diferencias entre
        distancias consecutivas ordenadas), usado para el criterio
        anti-aliasing Vs/f >= 2*dx de Park et al. None si no se puede
        estimar (menos de 2 distancias distintas)."""
        values = sorted(set(round(float(d), 6) for d in distances))
        if len(values) < 2:
            return None
        diffs = np.diff(np.asarray(values, dtype=np.float64))
        diffs = diffs[diffs > 1e-9]
        if diffs.size == 0:
            return None
        return float(np.median(diffs))

    @staticmethod
    def _estimate_array_length(distances: list[float]) -> float | None:
        """Largo del arreglo L = span de las distancias (max - min), que fija la
        longitud de onda maxima confiable lambda_max = L (c <= L*f). None si no
        se puede estimar (menos de 2 distancias distintas)."""
        values = sorted(set(round(float(d), 6) for d in distances))
        if len(values) < 2:
            return None
        span = float(values[-1] - values[0])
        return span if span > 1e-9 else None

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
        self._reset_all_modes()
        self.picks.update({float(fv): float(cv) for fv, cv in zip(freqs, c_obs)})
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
        # Zona de aliasing espacial: por debajo de c = 2*dx*f la longitud de
        # onda (lambda = c/f) es menor que 2*dx (Nyquist espacial del tendido)
        # y la imagen no es confiable. Se enmascara a NaN para NO graficarla y
        # se excluye del picking (ver _valid_velocity_mask). El array completo
        # queda en _last_result para que la matematica del pick tenga la grilla
        # entera; solo el display se enmascara.
        A_display = np.asarray(A, dtype=np.float64).copy()
        dx = self._geophone_spacing_m
        length = self._array_length_m
        if dx and dx > 0:
            alias_c = 2.0 * dx * f[:, None]  # (n_f, 1) velocidad minima valida por frecuencia
            A_display[c[None, :] < alias_c] = np.nan
        if length and length > 0:
            lam_c = length * f[:, None]  # (n_f, 1) velocidad maxima valida (lambda <= L)
            A_display[c[None, :] > lam_c] = np.nan
        self.image_view.setImage(A_display, pos=[float(f[0]), float(c[0])], scale=[df, dc], autoRange=True)
        self._plot_item.setLabel("bottom", "Frecuencia [Hz]")
        self._plot_item.setLabel("left", "Velocidad de fase [m/s]")
        try:
            self.image_view.setColorMap(pg.colormap.get("viridis"))
        except Exception:
            pass
        self._draw_alias_boundary(f, c)
        self._draw_lambda_max_boundary(f, c)
        self._refresh_pick_scatter()
        parts = []
        if dx and dx > 0:
            parts.append(f"c ≥ 2·dx·f (dx={dx:.2f} m, λ ≥ {2.0 * dx:.2f} m)")
        if length and length > 0:
            parts.append(f"c ≤ L·f (L={length:.1f} m, λ ≤ L)")
        if parts:
            alias_txt = "Banda válida de picking: " + " y ".join(parts) + "."
        else:
            alias_txt = "Sin espaciado ni largo de arreglo conocidos: no se aplican los limites de λ."
        self.alias_info_label.setText(alias_txt)
        self.info_label.setText(
            f"Imagen lista: {len(self._raw_distances)} canales, f<={f[-1]:.1f} Hz, "
            f"c=[{c[0]:.0f},{c[-1]:.0f}] m/s. Ahora marca la cresta: 'Auto-pick' o click manual."
        )
        self.calc_btn.setEnabled(True)

    def _alias_min_velocity(self, f_val: float) -> float:
        """Velocidad de fase minima valida a la frecuencia f_val por el
        criterio anti-aliasing espacial c >= 2*dx*f. 0 si no se conoce el
        espaciado (no se filtra)."""
        dx = self._geophone_spacing_m
        if not dx or dx <= 0:
            return 0.0
        return 2.0 * dx * float(f_val)

    def _lambda_max_velocity(self, f_val: float) -> float:
        """Velocidad de fase maxima valida a la frecuencia f_val por el limite
        de longitud de onda maxima lambda_max = L (largo del arreglo): como
        lambda = c/f, la condicion lambda <= L equivale a c <= L*f. +inf si no
        se conoce L (no se filtra por arriba)."""
        length = self._array_length_m
        if not length or length <= 0:
            return float("inf")
        return float(length) * float(f_val)

    @staticmethod
    def _region_bounds(polygon: list[tuple[float, float]] | None) -> tuple[float, float, float, float] | None:
        """Bounding box (f_min, f_max, c_min, c_max) de un poligono, o None."""
        if not polygon:
            return None
        fs = [p[0] for p in polygon]
        cs = [p[1] for p in polygon]
        return (min(fs), max(fs), min(cs), max(cs))

    @staticmethod
    def _point_in_polygon(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
        """Ray casting clasico: True si el punto (x, y) esta dentro del
        poligono (lista de vertices, no hace falta repetir el primero al final)."""
        n = len(poly)
        if n < 3:
            return False
        inside = False
        x0, y0 = poly[n - 1]
        for x1, y1 in poly:
            if (y1 > y) != (y0 > y):
                x_cross = x1 + (y - y1) * (x0 - x1) / (y0 - y1)
                if x < x_cross:
                    inside = not inside
            x0, y0 = x1, y1
        return inside

    def _valid_velocity_mask(
        self, f_val: float, c: np.ndarray, polygon: list[tuple[float, float]] | None = None
    ) -> np.ndarray:
        """Mascara booleana sobre el array de velocidades c: True donde un
        pick a la frecuencia f_val es valido: banda de longitud de onda
        confiable 2*dx <= lambda <= L (o sea 2*dx*f <= c <= L*f) y, si se pasa
        un poligono de region, ademas cae dentro de la region encerrada."""
        mask = (c >= self._alias_min_velocity(f_val)) & (c <= self._lambda_max_velocity(f_val))
        if polygon:
            inside = np.array(
                [self._point_in_polygon(float(f_val), float(cv), polygon) for cv in c],
                dtype=bool,
            )
            mask &= inside
        return mask

    # --------------------------------------------------- modos (multi-modo)

    def _all_modes(self) -> list[int]:
        """Todos los modos existentes (con picks y/o regiones), incluyendo el 0."""
        modes = set(self.picks_by_mode.keys()) | set(self._regions_by_mode.keys())
        modes.add(0)
        return sorted(modes)

    def _regions_for_mode(self, mode: int) -> list[list[tuple[float, float]]]:
        return self._regions_by_mode.get(mode, [])

    def _refresh_mode_legend(self) -> None:
        if not hasattr(self, "mode_legend_label"):
            return
        chips = []
        for m in self._all_modes():
            color = self._mode_color(m)
            weight = "bold" if m == self._active_mode else "normal"
            chips.append(
                f"<span style='color:{color}; font-weight:{weight}'>&#9632; M{m}</span>"
            )
        self.mode_legend_label.setText("&nbsp;&nbsp;".join(chips))

    def _refresh_mode_combo(self) -> None:
        if not hasattr(self, "active_mode_combo"):
            return
        modes = self._all_modes()
        self.active_mode_combo.blockSignals(True)
        self.active_mode_combo.clear()
        for m in modes:
            n = len(self.picks_by_mode.get(m, {}))
            r = len(self._regions_by_mode.get(m, []))
            tag = "M0 (fundamental)" if m == 0 else f"M{m}"
            self.active_mode_combo.addItem(f"{tag} — {r} reg, {n} picks", m)
        idx = modes.index(self._active_mode) if self._active_mode in modes else 0
        self.active_mode_combo.setCurrentIndex(idx)
        self.active_mode_combo.blockSignals(False)
        self._active_mode = self.active_mode_combo.currentData()
        if self._active_mode is None:
            self._active_mode = 0
        self.picks = self.picks_by_mode.setdefault(self._active_mode, {})
        self._refresh_mode_legend()

    def _active_mode_changed(self, _idx: int) -> None:
        m = self.active_mode_combo.currentData()
        if m is None:
            return
        self._active_mode = int(m)
        self.picks = self.picks_by_mode.setdefault(self._active_mode, {})
        self._regions_by_mode.setdefault(self._active_mode, [])
        self._dragging_pick = None
        self._inv_drag_key = None
        self._inv_drag_pos = None
        self._refresh_pick_scatter()
        self._refresh_m0_draw()
        self._refresh_inv_observed()
        self._refresh_mode_legend()
        self._inv_after_edit()

    def _add_mode(self) -> None:
        """Crea un modo nuevo (indice = max + 1) y lo deja activo, listo para
        definirle regiones y pickear."""
        new_mode = max(self._all_modes()) + 1
        self.picks_by_mode.setdefault(new_mode, {})
        self._regions_by_mode.setdefault(new_mode, [])
        self._active_mode = new_mode
        self.picks = self.picks_by_mode[new_mode]
        self._m0_draft = []
        self._m0_drawing = False
        if hasattr(self, "m0_close_btn"):
            self.m0_close_btn.setEnabled(False)
        self._refresh_mode_combo()
        self._refresh_pick_scatter()
        self._refresh_m0_draw()
        self._refresh_inv_observed()
        self.info_label.setText(
            f"Modo M{new_mode} creado y activo. Defini sus regiones con 'Iniciar región' y pickea; "
            "cambia entre modos con 'Modo activo'."
        )

    def _reset_all_modes(self) -> None:
        """Vuelve al estado de un solo modo vacio, sin regiones. Se usa al
        cargar datos nuevos o al arrancar un auto-pick de cero."""
        self.picks_by_mode = {0: {}}
        self._regions_by_mode = {0: []}
        self._active_mode = 0
        self.picks = self.picks_by_mode[0]
        self._m0_draft = []
        self._m0_drawing = False
        if hasattr(self, "m0_close_btn"):
            self.m0_close_btn.setEnabled(False)
        self._refresh_mode_combo()
        self._refresh_pick_scatter()
        self._refresh_m0_draw()
        self._refresh_inv_observed()

    def _velocity_mask_polys(
        self, f_val: float, c: np.ndarray, polys: list[list[tuple[float, float]]]
    ) -> np.ndarray:
        """Banda valida 2*dx*f <= c <= L*f, y si `polys` no esta vacio, ademas
        c cae dentro de la UNION de esos poligonos (regiones de un modo)."""
        mask = (c >= self._alias_min_velocity(f_val)) & (c <= self._lambda_max_velocity(f_val))
        if polys:
            inside = np.zeros(c.shape, dtype=bool)
            for poly in polys:
                inside |= np.array(
                    [self._point_in_polygon(float(f_val), float(cv), poly) for cv in c],
                    dtype=bool,
                )
            mask &= inside
        return mask

    def _auto_pick(self) -> None:
        if self._last_result is None:
            QMessageBox.warning(self, "MASW", "Primero calcula la imagen de dispersion.")
            return
        f, c, A = self._last_result
        f_lo = float(self.pick_fmin_spin.value())
        f_hi = float(self.pick_fmax_spin.value())
        # Un modo por cada modo que tenga regiones: su curva sale de la cresta
        # dentro de la UNION de sus regiones. Si ningun modo tiene regiones, un
        # solo pick sobre toda la imagen valida en el modo activo.
        modes_with_regions = [m for m in sorted(self._regions_by_mode) if self._regions_by_mode[m]]
        if modes_with_regions:
            targets = [(m, self._regions_by_mode[m]) for m in modes_with_regions]
        else:
            targets = [(self._active_mode, [])]
        results: dict[int, dict[float, float]] = {}
        for mode, polys in targets:
            picks: dict[float, float] = {}
            for i, f_val in enumerate(f):
                if f_val < f_lo or f_val > f_hi or f_val <= 0:
                    continue
                vmask = self._velocity_mask_polys(f_val, c, polys)
                if not np.any(vmask):
                    continue
                amp = np.where(vmask, A[i], -np.inf)
                j = int(np.argmax(amp))
                picks[float(f_val)] = float(c[j])
            results[mode] = picks
        total = sum(len(p) for p in results.values())
        if not total:
            QMessageBox.warning(
                self,
                "MASW",
                "Ningun bin de frecuencia valido cae en el rango de picking (revisa el rango, "
                "las regiones de los modos o los limites de longitud de onda).",
            )
            return
        # Registra la curva de cada modo en SU modo (no pisa modos sin regiones).
        for mode, picks in results.items():
            self.picks_by_mode[mode] = picks
            self._regions_by_mode.setdefault(mode, [])
        if self._active_mode not in self.picks_by_mode:
            self._active_mode = sorted(results)[0]
        self.picks = self.picks_by_mode.setdefault(self._active_mode, {})
        self._refresh_mode_combo()
        self._refresh_pick_scatter()
        self._refresh_inv_observed()
        per = ", ".join(f"M{m}:{len(results[m])}" for m in sorted(results))
        self.info_label.setText(
            f"Auto-pick: {per} puntos por modo. Elegi el 'Modo activo' para editar cada curva; "
            "'Correr inversion' usa TODAS las curvas."
        )

    def _clear_picks(self) -> None:
        """'Limpiar picks' limpia los picks del modo activo (no toca sus regiones
        ni los otros modos)."""
        self.picks_by_mode[self._active_mode] = {}
        self.picks = self.picks_by_mode[self._active_mode]
        self._refresh_mode_combo()
        self._refresh_pick_scatter()
        self._refresh_inv_observed()
        self.info_label.setText(f"Picks del modo M{self._active_mode} limpiados.")

    def _refresh_pick_scatter(self) -> None:
        spots = []
        for mode, picks in self.picks_by_mode.items():
            if not picks:
                continue
            color = self._mode_color(mode)
            active = mode == self._active_mode
            size = 10 if active else 7
            pen = pg.mkPen("#ffffff", width=1.5) if active else pg.mkPen("#000000", width=1)
            for fv in sorted(picks):
                spots.append(
                    {
                        "pos": (float(fv), float(picks[fv])),
                        "brush": pg.mkBrush(color),
                        "pen": pen,
                        "size": size,
                    }
                )
        self.pick_scatter.setData(spots=spots)

    def _set_pick_mode(self, mode: str) -> None:
        self._pick_mode = mode
        self._dragging_pick = None

    def _draw_alias_boundary(self, f: np.ndarray, c: np.ndarray) -> None:
        """Dibuja la linea c = 2*dx*f (borde de la zona de aliasing) sobre la
        imagen de dispersion, para que se vea de un vistazo hasta donde es
        confiable el pick. Se recorta al rango de velocidad de la imagen."""
        if self._alias_line is not None:
            try:
                self._plot_item.removeItem(self._alias_line)
            except Exception:
                pass
            self._alias_line = None
        dx = self._geophone_spacing_m
        if not dx or dx <= 0 or f.size == 0:
            return
        c_alias = 2.0 * dx * f
        inside = c_alias <= float(c[-1])
        if not np.any(inside):
            return
        pen = pg.mkPen("#ff3b30", width=2, style=Qt.PenStyle.DashLine)
        self._alias_line = self._plot_item.plot(
            f[inside], np.clip(c_alias[inside], float(c[0]), float(c[-1])), pen=pen
        )
        self._alias_line.setZValue(40)

    def _draw_lambda_max_boundary(self, f: np.ndarray, c: np.ndarray) -> None:
        """Dibuja la linea c = L*f (borde de longitud de onda maxima lambda=L,
        largo del arreglo) sobre la imagen: por encima de ella lambda > L y el
        pick no es confiable. Se recorta al rango de velocidad de la imagen."""
        if self._lambda_max_line is not None:
            try:
                self._plot_item.removeItem(self._lambda_max_line)
            except Exception:
                pass
            self._lambda_max_line = None
        length = self._array_length_m
        if not length or length <= 0 or f.size == 0:
            return
        c_lam = length * f
        inside = c_lam >= float(c[0])
        if not np.any(inside):
            return
        pen = pg.mkPen("#ffcc00", width=2, style=Qt.PenStyle.DashLine)
        self._lambda_max_line = self._plot_item.plot(
            f[inside], np.clip(c_lam[inside], float(c[0]), float(c[-1])), pen=pen
        )
        self._lambda_max_line.setZValue(40)

    def _start_m0_polygon(self) -> None:
        if self._last_result is None:
            QMessageBox.warning(self, "MASW", "Primero calcula la imagen de dispersion.")
            return
        if self._m0_drawing:
            self.info_label.setText("Ya estas dibujando una region; cerrala o limpiá antes de empezar otra.")
            return
        # Empieza una region NUEVA para el MODO ACTIVO. Los clicks agregan
        # vertices; al cerrarla se agrega a la lista de regiones de ese modo.
        self._m0_draft = []
        self._m0_drawing = True
        self.m0_close_btn.setEnabled(False)
        self._refresh_m0_draw()
        self.info_label.setText(
            f"Dibujando región para el modo M{self._active_mode}: hace click en la imagen para "
            "agregar vertices (punto a punto). Con al menos 3 vertices presiona 'Cerrar región'."
        )

    def _close_m0_polygon(self) -> None:
        if not self._m0_drawing or len(self._m0_draft) < 3:
            self.info_label.setText("Necesitas al menos 3 vertices para cerrar la region.")
            return
        mode = self._active_mode
        self._regions_by_mode.setdefault(mode, []).append(list(self._m0_draft))
        self.picks_by_mode.setdefault(mode, {})
        n_regions = len(self._regions_by_mode[mode])
        self._m0_draft = []
        self._m0_drawing = False
        self.m0_close_btn.setEnabled(False)
        self._refresh_m0_draw()
        self._refresh_mode_combo()
        self.info_label.setText(
            f"Región agregada al modo M{mode} ({n_regions} región/es en M{mode}). Agrega mas "
            "regiones a este modo, crea otro con '+ Agregar modo', o corre 'Auto-pick'."
        )

    def _clear_m0_region(self) -> None:
        mode = self._active_mode
        had = bool(self._regions_by_mode.get(mode)) or self._m0_drawing or bool(self._m0_draft)
        self._regions_by_mode[mode] = []
        self._m0_draft = []
        self._m0_drawing = False
        self.m0_close_btn.setEnabled(False)
        self._refresh_m0_draw()
        self._refresh_mode_combo()
        if had:
            self.info_label.setText(
                f"Regiones del modo M{mode} quitadas (los picks quedan)."
            )

    def _refresh_m0_draw(self) -> None:
        """Redibuja las regiones de TODOS los modos (cada una cerrada, en el
        color de su modo, con etiqueta M0/M1/...) mas la region en construccion
        (en el color del modo activo)."""
        if self._m0_line is None or self._m0_vertices is None:
            return
        for lbl in self._region_labels:
            try:
                self._plot_item.removeItem(lbl)
            except Exception:
                pass
        self._region_labels = []
        line_x: list[float] = []
        line_y: list[float] = []
        vert_spots = []

        def _append_break() -> None:
            if line_x:
                line_x.append(np.nan)
                line_y.append(np.nan)

        for mode in sorted(self._regions_by_mode):
            color = self._mode_color(mode)
            for poly in self._regions_by_mode[mode]:
                if len(poly) < 2:
                    continue
                xs = [p[0] for p in poly]
                ys = [p[1] for p in poly]
                _append_break()
                line_x.extend(xs + [xs[0]])
                line_y.extend(ys + [ys[0]])
                for x, y in poly:
                    vert_spots.append(
                        {"pos": (float(x), float(y)), "brush": pg.mkBrush(color),
                         "pen": pg.mkPen("#000000", width=1), "size": 8}
                    )
                lbl = pg.TextItem(f"M{mode}", color=color, anchor=(0.5, 1.0))
                lbl.setPos(float(np.mean(xs)), float(np.max(ys)))
                lbl.setZValue(47)
                self._plot_item.addItem(lbl)
                self._region_labels.append(lbl)

        if self._m0_drawing and self._m0_draft:
            color = self._mode_color(self._active_mode)
            dxs = [p[0] for p in self._m0_draft]
            dys = [p[1] for p in self._m0_draft]
            _append_break()
            line_x.extend(dxs)
            line_y.extend(dys)
            for x, y in self._m0_draft:
                vert_spots.append(
                    {"pos": (float(x), float(y)), "brush": pg.mkBrush(color),
                     "pen": pg.mkPen("#ffffff", width=1), "size": 8}
                )

        if line_x:
            self._m0_line.setData(line_x, line_y, connect="finite")
        else:
            self._m0_line.setData([], [])
        self._m0_vertices.setData(spots=vert_spots)

    def _on_mouse_clicked(self, event) -> None:
        if self._last_result is None:
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self._plot_item.sceneBoundingRect().contains(event.scenePos()):
            return
        point = self._plot_item.vb.mapSceneToView(event.scenePos())
        f_val, c_val = float(point.x()), float(point.y())
        f, c, _A = self._last_result
        # Mientras se dibuja el poligono M0 el click agrega un vertice y no
        # toca los picks (tiene prioridad sobre los modos de picking).
        if self._m0_drawing:
            f_clipped = float(np.clip(f_val, f[0], f[-1]))
            c_clipped = float(np.clip(c_val, c[0], c[-1]))
            self._m0_draft.append((f_clipped, c_clipped))
            self._refresh_m0_draw()
            self.m0_close_btn.setEnabled(len(self._m0_draft) >= 3)
            self.info_label.setText(
                f"Poligono M0: {len(self._m0_draft)} vertice(s). Segui clickeando y presiona "
                "'Cerrar polígono M0' cuando tengas al menos 3."
            )
            event.accept()
            return
        if self._pick_mode in ("ver", "arrastrar"):
            return
        if self._pick_mode == "anadir":
            if not (f[0] <= f_val <= f[-1]) or f_val <= 0:
                return
            idx = int(np.argmin(np.abs(f - f_val)))
            key = float(f[idx])
            if key in self.picks:
                self.info_label.setText(
                    f"Ya hay un pick en f={key:.2f} Hz. Usa 'Arrastrar' para moverlo o 'Borrar' para sacarlo."
                )
                return
            c_clipped = float(np.clip(c_val, c[0], c[-1]))
            alias_min = self._alias_min_velocity(key)
            if c_clipped < alias_min:
                self.info_label.setText(
                    f"Pick rechazado: c={c_clipped:.0f} m/s a {key:.2f} Hz cae en la zona de aliasing "
                    f"(minimo valido c={alias_min:.0f} m/s, lambda <= 2·dx)."
                )
                event.accept()
                return
            lambda_max_v = self._lambda_max_velocity(key)
            if c_clipped > lambda_max_v:
                self.info_label.setText(
                    f"Pick rechazado: c={c_clipped:.0f} m/s a {key:.2f} Hz supera lambda_max = L "
                    f"(maximo valido c={lambda_max_v:.0f} m/s, lambda <= L={self._array_length_m:.1f} m)."
                )
                event.accept()
                return
            active_regions = self._regions_for_mode(self._active_mode)
            if active_regions and not any(
                self._point_in_polygon(key, c_clipped, poly) for poly in active_regions
            ):
                self.info_label.setText(
                    f"Pick rechazado: c={c_clipped:.0f} m/s a {key:.2f} Hz cae fuera de las regiones del modo activo (M{self._active_mode})."
                )
                event.accept()
                return
            self.picks[key] = c_clipped
            self._refresh_pick_scatter()
            event.accept()
        elif self._pick_mode == "borrar":
            if not self.picks:
                return
            nearest = min(self.picks, key=lambda k: abs(k - f_val))
            del self.picks[nearest]
            self._refresh_pick_scatter()
            event.accept()

    def _vb_mouse_drag_event(self, ev, axis=None) -> None:
        """Override del drag de la ViewBox: en modo 'arrastrar', si el drag
        arranca cerca de un pick existente, lo agarra y le mueve la
        velocidad (la frecuencia del pick no cambia). Fuera de ese caso,
        delega al comportamiento default de pyqtgraph (pan de la vista)."""
        if self._pick_mode != "arrastrar" or self._last_result is None or ev.button() != Qt.MouseButton.LeftButton:
            self._dragging_pick = None
            self._default_drag_event(ev, axis=axis)
            return
        f, c, _A = self._last_result
        if ev.isStart():
            self._dragging_pick = None
            if self.picks:
                start_point = self._plot_item.vb.mapSceneToView(ev.buttonDownScenePos())
                f_val = float(start_point.x())
                nearest = min(self.picks, key=lambda k: abs(k - f_val))
                f_range = float(f[-1] - f[0]) if f.size > 1 else 1.0
                bin_width = float(f[1] - f[0]) if f.size > 1 else f_range
                tolerance = max(f_range * 0.04, bin_width)
                if abs(nearest - f_val) <= tolerance:
                    self._dragging_pick = nearest
        if self._dragging_pick is None:
            self._default_drag_event(ev, axis=axis)
            return
        point = self._plot_item.vb.mapSceneToView(ev.scenePos())
        c_val = float(np.clip(point.y(), c[0], c[-1]))
        self.picks[self._dragging_pick] = c_val
        self._refresh_pick_scatter()
        ev.accept()
        if ev.isFinish():
            self._dragging_pick = None

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

    def _plot_observed_curve(self, freqs: np.ndarray | None = None, c_obs: np.ndarray | None = None) -> None:
        # La curva experimental se dibuja siempre desde los picks actuales
        # (item persistente y editable). Los argumentos quedan por compat con
        # las llamadas viejas del flujo de inversion.
        self._refresh_inv_observed()

    # -------------------------------- edicion de la curva en la inversion

    def _set_inv_edit_mode(self, mode: str) -> None:
        self._inv_edit_mode = mode
        self._inv_drag_key = None
        self._inv_drag_pos = None

    def _inv_observed_points(self) -> tuple[np.ndarray, np.ndarray]:
        """Puntos (f, c) de la curva experimental para la pestaña de inversion,
        aplicando el punto que se este arrastrando en ese momento."""
        items = dict(self.picks)
        if self._inv_drag_key is not None and self._inv_drag_pos is not None:
            items.pop(self._inv_drag_key, None)
            f_d, c_d = self._inv_drag_pos
            items[f_d] = c_d
        fs = np.array(sorted(items), dtype=np.float64)
        cs = np.array([items[f] for f in fs], dtype=np.float64) if fs.size else np.array([], dtype=np.float64)
        return fs, cs

    def _refresh_inv_observed(self) -> None:
        if self._inv_observed_item is None:
            return
        fs, cs = self._inv_observed_points()
        self._inv_observed_item.setData(fs, cs)

    def _inv_after_edit(self) -> None:
        n = len(self.picks)
        if n < 3:
            self.inv_status_label.setText(
                f"Curva con {n} punto(s): se necesitan al menos 3 para invertir."
            )
        else:
            fs = sorted(self.picks)
            self.inv_status_label.setText(
                f"Curva editada: {n} puntos, f=[{fs[0]:.1f},{fs[-1]:.1f}] Hz. "
                "Corre la inversion cuando estes conforme."
            )

    def _inv_nearest_pick(self, f_val: float, c_val: float) -> float | None:
        """Clave (frecuencia) del pick mas cercano al punto (f_val, c_val) en la
        vista de inversion, o None si ninguno cae lo bastante cerca. La
        distancia se normaliza por el rango visible de cada eje para que
        'cerca' sea cerca en pantalla y no en unidades fisicas (Hz vs m/s)."""
        if not self.picks:
            return None
        vb = self.inv_plot.getPlotItem().vb
        (x0, x1), (y0, y1) = vb.viewRange()
        fspan = (x1 - x0) or 1.0
        cspan = (y1 - y0) or 1.0
        best_key: float | None = None
        best_d: float | None = None
        for k, cv in self.picks.items():
            dx = (k - f_val) / fspan
            dy = (cv - c_val) / cspan
            d = dx * dx + dy * dy
            if best_d is None or d < best_d:
                best_d = d
                best_key = k
        if best_d is not None and best_d <= 0.03 ** 2:
            return best_key
        return None

    def _on_inv_mouse_clicked(self, event) -> None:
        if self._inverting or self._inv_edit_mode != "borrar":
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        plot_item = self.inv_plot.getPlotItem()
        if not plot_item.sceneBoundingRect().contains(event.scenePos()):
            return
        point = plot_item.vb.mapSceneToView(event.scenePos())
        nearest = self._inv_nearest_pick(float(point.x()), float(point.y()))
        if nearest is None:
            return
        del self.picks[nearest]
        self._refresh_inv_observed()
        self._refresh_pick_scatter()
        self._inv_after_edit()
        event.accept()

    def _inv_vb_mouse_drag_event(self, ev, axis=None) -> None:
        """Override del drag de la ViewBox de la curva de inversion: en modo
        'mover', si el drag arranca cerca de un pick, lo agarra y lo mueve
        libremente en frecuencia y velocidad; al soltar se re-clava el pick en
        su nueva frecuencia. Fuera de ese caso, pan normal de pyqtgraph."""
        vb = self.inv_plot.getPlotItem().vb
        if self._inverting or self._inv_edit_mode != "mover" or ev.button() != Qt.MouseButton.LeftButton:
            self._inv_drag_key = None
            self._inv_drag_pos = None
            self._inv_default_drag_event(ev, axis=axis)
            return
        if ev.isStart():
            self._inv_drag_key = None
            self._inv_drag_pos = None
            start_point = vb.mapSceneToView(ev.buttonDownScenePos())
            nearest = self._inv_nearest_pick(float(start_point.x()), float(start_point.y()))
            if nearest is not None:
                self._inv_drag_key = nearest
                self._inv_drag_pos = (float(nearest), float(self.picks[nearest]))
        if self._inv_drag_key is None:
            self._inv_default_drag_event(ev, axis=axis)
            return
        point = vb.mapSceneToView(ev.scenePos())
        f_new = float(point.x())
        if f_new <= 0:
            f_new = 1e-6
        self._inv_drag_pos = (f_new, float(point.y()))
        self._refresh_inv_observed()
        ev.accept()
        if ev.isFinish():
            old_key = self._inv_drag_key
            pos = self._inv_drag_pos
            self._inv_drag_key = None
            self._inv_drag_pos = None
            if old_key is not None and pos is not None:
                self.picks.pop(old_key, None)
                self.picks[pos[0]] = pos[1]
            self._refresh_inv_observed()
            self._refresh_pick_scatter()
            self._inv_after_edit()

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
        # curva de dispersion como en el Earth (Vs) Model. La curva medida es
        # el item editable persistente; solo se re-crean los modelos teoricos.
        for item in self._inv_model_items:
            try:
                self.inv_plot.removeItem(item)
            except Exception:
                pass
        self._inv_model_items = []
        self._refresh_inv_observed()
        self.earth_plot.clear()
        dashed = pg.mkPen("#888888", width=1.5, style=Qt.PenStyle.DashLine)
        green = pg.mkPen("#2ecc71", width=2.5)
        initial_dc_item = self.inv_plot.plot([], [], pen=dashed, name="Modelo inicial")
        best_dc_item = self.inv_plot.plot([], [], pen=green, name="Mejor modelo")
        self._inv_model_items = [initial_dc_item, best_dc_item]
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

    # -------------------------------------- inversion conjunta multi-modo

    def _run_multimodal_inversion(self) -> None:
        if self._inverting:
            return
        curves = {m: p for m, p in self.picks_by_mode.items() if len(p) >= 3}
        if not curves:
            QMessageBox.warning(
                self, "MASW",
                "Se necesita al menos un modo con 3+ picks. Defini regiones por modo y corre 'Auto-pick'.",
            )
            return
        if not masw_multimodal.available():
            QMessageBox.warning(
                self, "MASW",
                "La inversion multimodo necesita las librerias 'disba' y 'evodcinv'.\n"
                "Instalalas con:\n    pip install disba evodcinv",
            )
            return
        curves_by_mode = {
            int(m): (
                np.array(sorted(p), dtype=np.float64),
                np.array([p[k] for k in sorted(p)], dtype=np.float64),
            )
            for m, p in curves.items()
        }
        self._inverting = True
        self.run_mm_btn.setEnabled(False)
        self.run_inv_btn.setEnabled(False)
        self.inv_status_label.setText(
            f"Corriendo inversion multimodo ({len(curves_by_mode)} modos) con evodcinv+disba... puede tardar."
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            res = masw_multimodal.multimodal_inversion(
                curves_by_mode,
                n_layers=int(self.nlayers_spin.value()),
                maxiter=int(max(self.niter_spin.value() // 10, 30)),
                popsize=20,
            )
        except Exception as exc:
            QMessageBox.critical(self, "MASW", f"La inversion multimodo fallo: {exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
            self._inverting = False
            self.run_mm_btn.setEnabled(True)
            self.run_inv_btn.setEnabled(True)
        self._mm_result = res
        self._display_multimodal_result(res)

    def _display_multimodal_result(self, res: dict) -> None:
        for item in self._inv_model_items:
            try:
                self.inv_plot.removeItem(item)
            except Exception:
                pass
        self._inv_model_items = []
        self._refresh_inv_observed()
        items = []
        for m in res["modes"]:
            color = self._mode_color(m)
            picks = self.picks_by_mode.get(m, {})
            if picks:
                fs = np.array(sorted(picks), dtype=np.float64)
                cs = np.array([picks[k] for k in sorted(picks)], dtype=np.float64)
                obs = self.inv_plot.plot(
                    fs, cs, pen=None, symbol="o", symbolSize=7,
                    symbolBrush=color, name=f"M{m} medida",
                )
                items.append(obs)
            tf, tc = res["theoretical"].get(m, (np.array([]), np.array([])))
            tf = np.asarray(tf, dtype=np.float64)
            tc = np.asarray(tc, dtype=np.float64)
            if tf.size:
                order = np.argsort(tf)
                th = self.inv_plot.plot(
                    tf[order], tc[order],
                    pen=pg.mkPen(color, width=2, style=Qt.PenStyle.DashLine),
                    name=f"M{m} teorica",
                )
                items.append(th)
        self._inv_model_items = items

        beta = np.asarray(res["beta"], dtype=np.float64)
        h = np.asarray(res["h"], dtype=np.float64)
        z_total = float(np.sum(h))
        z_max = max(z_total * 1.4, z_total + 2.0)
        xs, ys = self._profile_steps(beta, h, z_max)
        green = pg.mkPen("#2ecc71", width=2.5)
        self.earth_plot.clear()
        self.earth_plot.plot(xs, ys, pen=green, name="Vs multimodo")
        self.profile_plot.clear()
        self.profile_plot.plot(xs, ys, pen=green, name="Vs multimodo")
        boundaries = np.concatenate(([0.0], np.cumsum(h)))
        lines = [
            "INVERSION MULTIMODO (evodcinv + disba)",
            f"Modos: {', '.join('M' + str(m) for m in res['modes'])}",
            f"Desajuste (rmse): {res['misfit']:.4f} km/s",
            "",
        ]
        for i in range(h.size):
            lines.append(
                f"Capa {i + 1}: {boundaries[i]:.2f}-{boundaries[i + 1]:.2f} m | Vs = {beta[i]:.0f} m/s"
            )
        lines.append(f"Semiespacio (>{boundaries[-1]:.2f} m): Vs = {beta[-1]:.0f} m/s")
        self.profile_summary.setText("\n".join(lines))
        self.inv_status_label.setText(
            f"Inversion multimodo lista: {len(res['modes'])} modos, desajuste {res['misfit']:.4f} km/s. "
            "El perfil esta en '3. Perfil Vs'."
        )
        self.inner_tabs.setCurrentIndex(2)

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

    def _redraw_inversion_from_result(self) -> None:
        """Redibuja la curva de dispersion y el Earth (Vs) Model de la pestaña
        de inversion desde `self._inv_result`, sin volver a correr la inversion
        (se usa al restaurar el ultimo analisis guardado)."""
        r = self._inv_result
        if not r:
            return
        for item in self._inv_model_items:
            try:
                self.inv_plot.removeItem(item)
            except Exception:
                pass
        self._inv_model_items = []
        self._refresh_inv_observed()
        self.earth_plot.clear()
        dashed = pg.mkPen("#888888", width=1.5, style=Qt.PenStyle.DashLine)
        green = pg.mkPen("#2ecc71", width=2.5)
        beta = np.asarray(r["beta"], dtype=np.float64)
        h = np.asarray(r["h"], dtype=np.float64)
        z_total = float(np.sum(h))
        z_max = max(z_total * 1.4, z_total + 2.0)
        xs, ys = self._profile_steps(beta, h, z_max)
        freqs = np.asarray(r.get("freqs", []), dtype=np.float64)
        c_t = np.asarray(r.get("c_t", []), dtype=np.float64)
        valid = np.isfinite(c_t) if c_t.size else np.zeros(0, dtype=bool)
        if valid.size and np.any(valid):
            best_dc = self.inv_plot.plot(freqs[valid], c_t[valid], pen=green, name="Mejor modelo")
        else:
            best_dc = self.inv_plot.plot([], [], pen=green, name="Mejor modelo")
        self._inv_model_items = [best_dc]
        if "beta_initial" in r and "h_initial" in r:
            bi = np.asarray(r["beta_initial"], dtype=np.float64)
            hi = np.asarray(r["h_initial"], dtype=np.float64)
            xs0, ys0 = self._profile_steps(bi, hi, z_max)
            self.earth_plot.plot(xs0, ys0, pen=dashed, name="Modelo inicial")
        self.earth_plot.plot(xs, ys, pen=green, name="Mejor modelo")
        misfit = float(r.get("misfit", float("nan")))
        self.inv_status_label.setText(
            f"Resultado restaurado: desajuste {misfit:.2f} %. El perfil final esta en '3. Perfil Vs'."
        )

    # -------------------------------------------------- persistencia (feat 3)

    _INV_ARRAY_KEYS = (
        "beta", "h", "beta_initial", "h_initial",
        "freqs", "c_obs", "c_t", "alpha", "wavelengths",
    )

    def get_state(self) -> dict:
        """Estado liviano (JSON) del analisis MASW: parametros, picks por modo,
        regiones y escalares del resultado de inversion. Los arrays pesados van
        aparte en `get_arrays`."""
        return {
            "image_params": {
                "cmin": float(self.cmin_spin.value()),
                "cmax": float(self.cmax_spin.value()),
                "cstep": float(self.cstep_spin.value()),
                "fmin": float(self.fmin_spin.value()),
                "fmax": float(self.fmax_spin.value()),
            },
            "pick_range": {
                "fmin": float(self.pick_fmin_spin.value()),
                "fmax": float(self.pick_fmax_spin.value()),
            },
            "inversion_params": {
                "nlayers": int(self.nlayers_spin.value()),
                "niter": int(self.niter_spin.value()),
                "bs": float(self.bs_spin.value()),
                "bh": float(self.bh_spin.value()),
                "nu": float(self.nu_spin.value()),
                "rho": float(self.rho_spin.value()),
            },
            "active_mode": int(self._active_mode),
            "picks_by_mode": {
                str(mode): [[float(f), float(picks[f])] for f in sorted(picks)]
                for mode, picks in self.picks_by_mode.items()
            },
            "regions": [[[float(x), float(y)] for (x, y) in poly] for poly in self._regions],
            "geophone_spacing_m": (
                float(self._geophone_spacing_m) if self._geophone_spacing_m else None
            ),
            "array_length_m": (
                float(self._array_length_m) if self._array_length_m else None
            ),
            "inner_tab": int(self.inner_tabs.currentIndex()),
            "has_data": self._raw_matrix is not None,
            "has_inv_result": self._inv_result is not None,
            "inv_scalars": (
                {
                    "misfit": float(self._inv_result.get("misfit", float("nan"))),
                    "nu": float(self._inv_result.get("nu", float(self.nu_spin.value()))),
                }
                if self._inv_result is not None
                else {}
            ),
        }

    def get_arrays(self) -> dict:
        """Arrays pesados (npz) del analisis MASW: datos crudos que se mandaron
        a la imagen y arrays del resultado de inversion (con prefijos para
        compartir un unico .npz con el waterfall)."""
        arrays: dict = {}
        if self._raw_matrix is not None and self._raw_time is not None and self._raw_distances is not None:
            arrays["masw_time"] = np.asarray(self._raw_time, dtype=np.float64)
            arrays["masw_distances"] = np.asarray(self._raw_distances, dtype=np.float64)
            arrays["masw_matrix"] = np.asarray(self._raw_matrix, dtype=np.float64)
        r = self._inv_result
        if r is not None:
            for key in self._INV_ARRAY_KEYS:
                if key in r and r[key] is not None:
                    arrays[f"inv_{key}"] = np.asarray(r[key], dtype=np.float64)
        return arrays

    def restore_state(self, state: dict, arrays: dict | None) -> None:
        if not state:
            return
        arrays = arrays or {}
        ip = state.get("image_params", {})
        for spin, key in (
            (self.cmin_spin, "cmin"), (self.cmax_spin, "cmax"), (self.cstep_spin, "cstep"),
            (self.fmin_spin, "fmin"), (self.fmax_spin, "fmax"),
        ):
            if key in ip:
                spin.setValue(float(ip[key]))
        pr = state.get("pick_range", {})
        if "fmin" in pr:
            self.pick_fmin_spin.setValue(float(pr["fmin"]))
        if "fmax" in pr:
            self.pick_fmax_spin.setValue(float(pr["fmax"]))
        inv = state.get("inversion_params", {})
        for spin, key, cast in (
            (self.nlayers_spin, "nlayers", int), (self.niter_spin, "niter", int),
            (self.bs_spin, "bs", float), (self.bh_spin, "bh", float),
            (self.nu_spin, "nu", float), (self.rho_spin, "rho", float),
        ):
            if key in inv:
                spin.setValue(cast(inv[key]))

        if "masw_matrix" in arrays:
            self._raw_time = np.asarray(arrays["masw_time"], dtype=np.float64)
            self._raw_distances = [float(d) for d in arrays["masw_distances"]]
            self._raw_matrix = np.asarray(arrays["masw_matrix"], dtype=np.float64)
            self._geophone_spacing_m = (
                state.get("geophone_spacing_m") or self._estimate_spacing_m(self._raw_distances)
            )
            self._array_length_m = (
                state.get("array_length_m") or self._estimate_array_length(self._raw_distances)
            )

        self._regions = [
            [(float(x), float(y)) for (x, y) in poly] for poly in state.get("regions", [])
        ]
        pbm: dict[int, dict[float, float]] = {}
        for mode_str, pts in state.get("picks_by_mode", {}).items():
            try:
                mode = int(mode_str)
            except (TypeError, ValueError):
                continue
            pbm[mode] = {float(f): float(c) for f, c in pts}
        if not pbm:
            pbm = {0: {}}
        self.picks_by_mode = pbm
        self._active_mode = int(state.get("active_mode", 0))
        if self._active_mode not in self.picks_by_mode:
            self._active_mode = 0
        self.picks = self.picks_by_mode.setdefault(self._active_mode, {})

        if self._raw_matrix is not None:
            # Reconstruye la imagen de dispersion (deterministico) para que
            # picks y regiones se vean sobre ella y el auto-pick vuelva a andar.
            self._calculate()

        if state.get("has_inv_result") and "inv_beta" in arrays:
            r: dict = {}
            for key in self._INV_ARRAY_KEYS:
                arr_key = f"inv_{key}"
                if arr_key in arrays:
                    r[key] = np.asarray(arrays[arr_key], dtype=np.float64)
            scalars = state.get("inv_scalars", {})
            r["misfit"] = float(scalars.get("misfit", float("nan")))
            r["nu"] = float(scalars.get("nu", float(self.nu_spin.value())))
            self._inv_result = r
            self._redraw_inversion_from_result()
            self._update_profile_tab()

        self._refresh_mode_combo()
        self._refresh_pick_scatter()
        self._refresh_m0_draw()
        self._refresh_inv_observed()
        try:
            self.inner_tabs.setCurrentIndex(int(state.get("inner_tab", 0)))
        except Exception:
            pass

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


