"""PyQt reviewer for field hammer/geophone picks."""

from __future__ import annotations

import csv
import re
from datetime import datetime
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
    QSlider,
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
        auto_align_polarity,
        auto_pick_shot,
        build_waterfall_matrix,
        compute_average_groups,
        default_alignment_offsets_path,
        default_alignment_shot_offsets_path,
        default_average_arrivals_path,
        default_disabled_folders_path,
        default_dispersion_groups_path,
        default_filter_settings_path,
        default_masw_arrays_path,
        default_masw_state_path,
        default_output_dir,
        default_session_path,
        dispersion_groups_signature,
        disabled_folders_signature,
        export_processed,
        filter_settings_signature,
        fk_directional_filter,
        flip_distance_group,
        format_distance_label,
        get_alignment_offset,
        hammer_global_time_signal,
        load_alignment_offsets,
        load_alignment_shot_offsets,
        load_annotations,
        load_average_arrivals,
        load_disabled_folders,
        load_dispersion_groups,
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
        save_disabled_folders,
        save_dispersion_groups,
        save_filter_settings,
        save_masw_arrays,
        save_masw_state,
        save_session,
        zero_by_pretrigger,
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
        auto_align_polarity,
        auto_pick_shot,
        build_waterfall_matrix,
        compute_average_groups,
        default_alignment_offsets_path,
        default_alignment_shot_offsets_path,
        default_average_arrivals_path,
        default_disabled_folders_path,
        default_dispersion_groups_path,
        default_filter_settings_path,
        default_masw_arrays_path,
        default_masw_state_path,
        default_output_dir,
        default_session_path,
        dispersion_groups_signature,
        disabled_folders_signature,
        export_processed,
        filter_settings_signature,
        fk_directional_filter,
        flip_distance_group,
        format_distance_label,
        get_alignment_offset,
        hammer_global_time_signal,
        load_alignment_offsets,
        load_alignment_shot_offsets,
        load_annotations,
        load_average_arrivals,
        load_disabled_folders,
        load_dispersion_groups,
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
        save_disabled_folders,
        save_dispersion_groups,
        save_filter_settings,
        save_masw_arrays,
        save_masw_state,
        save_session,
        zero_by_pretrigger,
    )

try:
    from .masw_dispersion import (
        auto_extract_dispersion_curve,
        common_finite_window,
        phase_shift_dispersion_image,
    )
    from .masw_inversion import monte_carlo_inversion
    from . import masw_multimodal
    from . import masw_backends
except ImportError:  # pragma: no cover - script execution from this folder
    from masw_dispersion import (
        auto_extract_dispersion_curve,
        common_finite_window,
        phase_shift_dispersion_image,
    )
    from masw_inversion import monte_carlo_inversion
    import masw_multimodal
    import masw_backends


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


def _folder_group_id(folder_name: str, group_count: int, assignments: dict[str, int] | None) -> int:
    group_count = max(1, int(group_count or 1))
    assignments = assignments or {}
    try:
        group_id = int(assignments.get(str(folder_name), 1))
    except (TypeError, ValueError):
        group_id = 1
    return int(np.clip(group_id, 1, group_count))


def _group_name(group_id: int) -> str:
    return f"Grupo {int(group_id)}"


def _group_disabled_key(label: str, group_id: int) -> str:
    return f"{label}::grupo{int(group_id)}"


def _project_disabled_for_group(
    disabled: dict[str, list[str]] | None,
    group_id: int,
    group_count: int,
) -> dict[str, list[str]] | None:
    if not disabled:
        return disabled
    group_id = max(1, int(group_id or 1))
    group_count = max(1, int(group_count or 1))
    projected: dict[str, list[str]] = {}
    for key, folders in disabled.items():
        if "::grupo" in key:
            label, _, suffix = key.partition("::grupo")
            try:
                gid = int(suffix)
            except ValueError:
                continue
            if gid == group_id:
                projected.setdefault(label, []).extend(folders)
        elif group_count <= 1 or group_id == 1:
            # Compatibilidad: rechazos viejos sin grupo pertenecen al flujo
            # historico, que ahora queda como Grupo 1.
            projected.setdefault(key, []).extend(folders)
    return {label: sorted(set(folders)) for label, folders in projected.items() if folders}


class SortTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other) -> bool:  # type: ignore[override]
        if isinstance(other, QTableWidgetItem):
            a = self.data(Qt.ItemDataRole.UserRole)
            b = other.data(Qt.ItemDataRole.UserRole)
            if a is not None and b is not None:
                try:
                    return a < b
                except TypeError:
                    return str(a) < str(b)
        return super().__lt__(other)


def _folder_date_text_and_sort(folder: str, folder_path: Path | None = None) -> tuple[str, float]:
    match = re.search(r"(20\d{6})[_-](\d{6})", folder)
    if match:
        stamp = f"{match.group(1)}{match.group(2)}"
        try:
            dt = datetime.strptime(stamp, "%Y%m%d%H%M%S")
            return dt.strftime("%Y-%m-%d %H:%M:%S"), float(stamp)
        except ValueError:
            pass
    if folder_path is not None:
        try:
            ts = float(folder_path.stat().st_mtime)
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"), ts
        except OSError:
            pass
    return "", 0.0


def _filtered_dataset_for_group(
    dataset: FieldDataset,
    group_id: int,
    group_count: int,
    assignments: dict[str, int] | None,
) -> FieldDataset:
    group_id = int(np.clip(int(group_id or 1), 1, max(1, int(group_count or 1))))
    shots = [
        shot for shot in dataset.shots
        if _folder_group_id(shot.folder_name, group_count, assignments) == group_id
    ]
    return FieldDataset(
        raw_root=dataset.raw_root,
        shots=shots,
        duplicate_groups=dataset.duplicate_groups,
        skipped_folders=dataset.skipped_folders,
    )


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
        self.disabled_folders_path = default_disabled_folders_path(dataset.raw_root)
        self.disabled_folders = load_disabled_folders(self.disabled_folders_path)
        self.dispersion_groups_path = default_dispersion_groups_path(dataset.raw_root)
        self.group_count, self.group_assignments = load_dispersion_groups(self.dispersion_groups_path)
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
        # Cada restore en su propio try: si el del waterfall falla no debe
        # impedir restaurar los picks/regiones de MASW (que al cerrar se
        # re-guardan y pisarian el trabajo guardado si quedaran vacios).
        try:
            self.waterfall_panel.restore_state(
                self._masw_state.get("waterfall", {}),
                self._masw_arrays,
                self.average_panel.arrivals,
            )
        except Exception:
            pass
        try:
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
        if hasattr(self, "alignment_panel"):
            self.alignment_panel.restore_selection(
                group_id=self._session.get("alignment_group_id"),
                label=self._session.get("alignment_label"),
                folder=self._session.get("alignment_folder"),
            )
        if hasattr(self, "average_panel"):
            self.average_panel.restore_selection(
                group_id=self._session.get("average_group_id"),
            )

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
        if hasattr(self, "alignment_panel"):
            data.update(self.alignment_panel.selection_state())
        if hasattr(self, "average_panel"):
            data.update(self.average_panel.selection_state())
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
        self.folder_avg_check = QCheckBox("Promedio carpeta")
        self.folder_avg_check.setChecked(True)
        self.folder_avg_check.setToolTip(
            "Superpone el promedio (alineado por trigger) de las señales YA validadas\n"
            "de la MISMA CARPETA, sin contar la actual: referencia para dejar BIEN el\n"
            "trigger de cada señal (el ajuste fino); el desfase entre dias se calibra\n"
            "despues en Enfase, por carpeta."
        )
        self.folder_avg_check.toggled.connect(self._refresh_plot)
        overlay_box.addWidget(self.folder_avg_check)
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
            on_flip_distance=self._flip_distance_group,
            on_auto_polarity=self._auto_polarity,
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
            disabled_folders=self.disabled_folders,
            group_count=self.group_count,
            group_assignments=self.group_assignments,
        )
        self.filter_panel = FilterPanel(
            settings=self.filter_settings,
            get_current=self._filter_preview_data,
            dark_mode=self.dark_mode,
            on_changed=self._filter_settings_changed,
        )
        self.grouping_panel = GroupingPanel(
            dataset=self.dataset,
            annotations=self.annotations,
            group_count=self.group_count,
            assignments=self.group_assignments,
            dark_mode=self.dark_mode,
            on_changed=self._grouping_changed,
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
            disabled=self.disabled_folders,
            group_count=self.group_count,
            group_assignments=self.group_assignments,
        )
        self.tabs.addTab(self.filter_panel, "Filtros")
        self.tabs.addTab(self.grouping_panel, "Agrupamiento")
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
        elif widget is self.grouping_panel:
            self.grouping_panel.refresh()
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

    def _grouping_changed(self, group_count: int, assignments: dict[str, int]) -> None:
        self.group_count = max(1, int(group_count or 1))
        self.group_assignments = assignments
        try:
            save_dispersion_groups(self.dispersion_groups_path, self.group_count, self.group_assignments)
        except Exception:
            pass
        if hasattr(self, "alignment_panel"):
            self.alignment_panel.set_grouping(self.group_count, self.group_assignments)
        if hasattr(self, "average_panel"):
            self.average_panel.set_grouping(self.group_count, self.group_assignments)

    def _alignment_offsets_changed(self) -> None:
        try:
            save_alignment_offsets(self.alignment_offsets_path, self.alignment_offsets)
            save_alignment_shot_offsets(self.alignment_shot_offsets_path, self.alignment_shot_offsets)
            save_disabled_folders(self.disabled_folders_path, self.disabled_folders)
        except Exception:
            pass

    def _show_waterfall_tab(
        self, common_time, distances, matrix, arrivals, hammer_global, n_averages,
        group_id: int = 1, group_name: str | None = None
    ) -> None:
        self.waterfall_panel.populate(
            common_time, distances, matrix, arrivals, hammer_global, n_averages, group_id, group_name
        )
        self.tabs.setCurrentWidget(self.waterfall_panel)

    def _show_masw_tab(
        self, common_time, distances, matrix, group_id: int = 1, group_name: str | None = None
    ) -> None:
        self.masw_panel.set_group_data(common_time, distances, matrix, group_id=group_id, group_name=group_name)
        self.tabs.setCurrentWidget(self.masw_panel)

    def _auto_masw_tab(
        self, common_time, distances, matrix, group_id: int = 1, group_name: str | None = None
    ) -> None:
        self.tabs.setCurrentWidget(self.masw_panel)
        QApplication.processEvents()
        self.masw_panel.run_auto(common_time, distances, matrix, group_id=group_id, group_name=group_name)

    def _flip_distance_group(self, distance_m: float) -> None:
        """Boton 'Invertir traza' del waterfall: invierte el punto completo
        (geo_flip en todas sus capturas) y niega la fila en pantalla al
        instante. Apretar de nuevo lo revierte."""
        changed = flip_distance_group(self.dataset, self.annotations, distance_m, source="manual")
        label = format_distance_label(distance_m)
        if not changed:
            self.waterfall_panel.info_label.setText(f"No hay capturas a {label} para invertir.")
            return
        self._autosave_annotations()
        self.waterfall_panel.flip_row(distance_m)
        self._refresh_plot()
        self.waterfall_panel.info_label.setText(
            f"Polaridad invertida en {label}: geo_flip toggleado en {changed} capturas "
            "(persiste; afecta promedios, MASW y export)"
        )

    def _auto_polarity(self) -> None:
        """Boton 'Auto polaridad' del waterfall: corre las dos etapas de
        auto_align_polarity, persiste y recalcula promedios/waterfall si
        cambio algun punto validado."""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            report = auto_align_polarity(
                self.dataset,
                self.annotations,
                prefer_filtered=self.prefer_filtered,
                filter_settings=self.filter_settings,
                alignment_offsets=self.alignment_offsets,
                alignment_shot_offsets=self.alignment_shot_offsets,
                disabled_folders=self.disabled_folders,
            )
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Auto polaridad", str(exc))
            return
        QApplication.restoreOverrideCursor()
        flipped_a = list(report.get("stage_a_flipped", []))
        flipped_b = list(report.get("stage_b_flipped_distances", []))
        skipped_b = list(report.get("stage_b_skipped_distances", []))
        if flipped_a or flipped_b:
            self._autosave_annotations()
            self._refresh_plot()
        if flipped_b:
            # Cambiaron puntos validados: los promedios y el waterfall que se
            # ven tienen la polaridad vieja, recalcular y re-mostrar.
            self.average_panel.refresh(force=True)
            if self.average_panel.averages:
                self.average_panel.show_waterfall()

        lines: list[str] = []
        if flipped_a:
            shown = ", ".join(flipped_a[:12]) + (", ..." if len(flipped_a) > 12 else "")
            lines.append(
                f"Etapa intra-punto: {len(flipped_a)} captura(s) SIN validar invertidas "
                f"para quedar en fase con las validadas de su punto (las validadas no se tocaron; "
                f"acepta las propuestas al revisarlas en Capturas):\n  {shown}"
            )
        if flipped_b:
            labels = ", ".join(format_distance_label(d) for d in flipped_b)
            lines.append(f"Etapa inter-punto: punto(s) completo(s) invertidos por contrafase: {labels}")
        if skipped_b:
            labels = ", ".join(format_distance_label(d) for d in sorted(set(skipped_b)))
            lines.append(f"Sin promedio validado (no participaron del enfase entre puntos): {labels}")
        if not lines:
            lines.append("No hizo falta ningun cambio: todos los puntos ya estan en fase.")
        lines.append(
            "\nSi algun punto quedo al reves igual, usa 'Invertir traza' para corregirlo a mano."
        )
        QMessageBox.information(self, "Auto polaridad", "\n\n".join(lines))

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
        self._plot_folder_average(shot)
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

    _FOLDER_AVERAGE_COLOR = "#2ca02c"

    def _folder_average_for_shot(
        self, current_shot: FieldShot
    ) -> tuple[np.ndarray, np.ndarray, int] | None:
        """Promedio del geofono (alineado por trigger) de las señales YA
        validadas (accepted+reviewed) de la MISMA CARPETA que la actual,
        excluyendola. Misma mecanica liviana que `_ok_average_for_distance`
        (cache de señales, sin resamplear fs distintas — dentro de una
        carpeta la fs es una sola)."""
        segments: list[tuple[np.ndarray, int]] = []
        fs_ref: float | None = None
        for shot in self.dataset.shots:
            if shot.folder_name != current_shot.folder_name:
                continue
            if shot.shot_id == current_shot.shot_id:
                continue
            ann = self.annotations.get(shot.shot_id)
            if ann is None or not ann.accepted or not ann.reviewed:
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
        return time_s, mean, len(segments)

    def _plot_folder_average(self, current_shot: FieldShot) -> None:
        if not self.folder_avg_check.isChecked():
            return
        result = self._folder_average_for_shot(current_shot)
        if result is None:
            return
        time_s, mean, n = result
        pen = pg.mkPen(self._FOLDER_AVERAGE_COLOR, width=2.5)
        item = self.geo_plot.plot(time_s, mean, pen=pen, name=f"promedio carpeta (n={n})")
        item.setZValue(28)

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
        ann.reviewed = True
        ann.accepted = bool(checked)
        ann.source = "manual"
        self._update_table_row(self._current_row, shot)
        self._refresh_plot()
        self._autosave_annotations()
        estado, _color = self._estado_display(ann.reviewed, ann.accepted)
        self.status_label.setText(f"{shot.folder_name}/{shot.capture_name}: {estado}")

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
        if hasattr(self, "grouping_panel"):
            self.grouping_panel.set_dark_mode(self.dark_mode)
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
        # Una sola implementación, en field_review_data: la web grafica llamando
        # a la misma, así los dos dibujos no se pueden desincronizar.
        return zero_by_pretrigger(signal, trigger_idx, fs)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            save_annotations(self.annotations_path, self.dataset, self.annotations)
            self.average_panel.save_arrivals()
            save_filter_settings(self.filter_settings_path, self.filter_settings)
            save_alignment_offsets(self.alignment_offsets_path, self.alignment_offsets)
            save_alignment_shot_offsets(self.alignment_shot_offsets_path, self.alignment_shot_offsets)
            save_disabled_folders(self.disabled_folders_path, self.disabled_folders)
            save_dispersion_groups(self.dispersion_groups_path, self.group_count, self.group_assignments)
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


class GroupingPanel(QWidget):
    """Asigna carpetas/tandas a grupos independientes para MASW.

    Cada grupo se procesa como un flujo completo: Enfase -> Promedios ->
    Waterfall -> imagen de dispersion. Si una carpeta no aparece en la
    asignacion persistida, pertenece al grupo 1.
    """

    def __init__(
        self,
        dataset: FieldDataset,
        annotations: dict[str, PickAnnotation],
        group_count: int,
        assignments: dict[str, int],
        dark_mode: bool = False,
        on_changed=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.dataset = dataset
        self.annotations = annotations
        self.group_count = max(1, int(group_count or 1))
        self.assignments = assignments
        self.dark_mode = dark_mode
        self.on_changed = on_changed
        self._loading = False
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        root = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(root)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)

        config_box = QGroupBox("Grupos de dispersion")
        form = QFormLayout(config_box)
        self.group_count_spin = QSpinBox()
        self.group_count_spin.setRange(1, 20)
        self.group_count_spin.setValue(self.group_count)
        self.group_count_spin.valueChanged.connect(self._group_count_changed)
        form.addRow("Cantidad", self.group_count_spin)
        left_layout.addWidget(config_box)

        assign_box = QGroupBox("Asignar carpetas seleccionadas")
        assign_layout = QVBoxLayout(assign_box)
        row = QHBoxLayout()
        row.addWidget(QLabel("Grupo"))
        self.assign_group_spin = QSpinBox()
        self.assign_group_spin.setRange(1, self.group_count)
        self.assign_group_spin.setValue(1)
        row.addWidget(self.assign_group_spin)
        self.assign_btn = QPushButton("Asignar")
        self.assign_btn.clicked.connect(self._assign_selected)
        row.addWidget(self.assign_btn)
        assign_layout.addLayout(row)
        self.all_g1_btn = QPushButton("Todo a Grupo 1")
        self.all_g1_btn.clicked.connect(self._all_to_group_one)
        self.folder_groups_btn = QPushButton("Una carpeta por grupo")
        self.folder_groups_btn.clicked.connect(self._one_group_per_folder)
        assign_layout.addWidget(self.all_g1_btn)
        assign_layout.addWidget(self.folder_groups_btn)
        left_layout.addWidget(assign_box)

        hint = QLabel(
            "Cada carpeta/tanda pertenece a un grupo. Despues elegis el grupo activo en Enfase y "
            "Promedios; cada grupo genera su propio waterfall y su propia imagen MASW. En MASW se "
            "combinan las imagenes normalizadas con pesos."
        )
        hint.setWordWrap(True)
        left_layout.addWidget(hint)
        left_layout.addStretch(1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 4, 8, 8)
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        right_layout.addWidget(self.summary_label)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Grupo", "Carpeta", "Capturas", "Distancias", "OK", "Fecha carpeta"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSortingEnabled(True)
        right_layout.addWidget(self.table, stretch=1)

        root.addWidget(left)
        root.addWidget(right)
        root.setSizes([320, 900])

    def _folders(self) -> list[str]:
        return sorted({shot.folder_name for shot in self.dataset.shots})

    def refresh(self) -> None:
        folders = self._folders()
        for folder in folders:
            self.assignments.setdefault(folder, 1)
        self._clamp_assignments()
        self._populate_table()

    def _clamp_assignments(self) -> None:
        self.group_count = max(1, int(self.group_count or 1))
        valid = set(self._folders())
        for folder in list(self.assignments):
            if folder not in valid:
                self.assignments.pop(folder, None)
                continue
            self.assignments[folder] = _folder_group_id(folder, self.group_count, self.assignments)

    def _emit_changed(self) -> None:
        self._clamp_assignments()
        if self.on_changed is not None:
            self.on_changed(self.group_count, self.assignments)

    def _group_count_changed(self, value: int) -> None:
        if self._loading:
            return
        self.group_count = max(1, int(value))
        self.assign_group_spin.setRange(1, self.group_count)
        self._emit_changed()
        self._populate_table()

    def _selected_folders(self) -> list[str]:
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        folders: list[str] = []
        for row in rows:
            item = self.table.item(row, 1)
            if item is not None:
                folders.append(item.text())
        return folders

    def _assign_selected(self) -> None:
        folders = self._selected_folders()
        if not folders:
            self.summary_label.setText("Selecciona una o mas carpetas para asignarlas.")
            return
        group_id = int(self.assign_group_spin.value())
        for folder in folders:
            self.assignments[folder] = group_id
        self._emit_changed()
        self._populate_table()

    def _all_to_group_one(self) -> None:
        self.group_count = max(1, int(self.group_count_spin.value()))
        for folder in self._folders():
            self.assignments[folder] = 1
        self._emit_changed()
        self._populate_table()

    def _one_group_per_folder(self) -> None:
        folders = self._folders()
        if not folders:
            return
        self._loading = True
        self.group_count = min(len(folders), self.group_count_spin.maximum())
        self.group_count_spin.setValue(self.group_count)
        self.assign_group_spin.setRange(1, self.group_count)
        self._loading = False
        for idx, folder in enumerate(folders, start=1):
            self.assignments[folder] = min(idx, self.group_count)
        self._emit_changed()
        self._populate_table()

    def _populate_table(self) -> None:
        folders = self._folders()
        by_folder: dict[str, dict[str, object]] = {
            folder: {"count": 0, "ok": 0, "distances": set(), "distance_values": set(), "path": None}
            for folder in folders
        }
        for shot in self.dataset.shots:
            info = by_folder.setdefault(
                shot.folder_name,
                {"count": 0, "ok": 0, "distances": set(), "distance_values": set(), "path": None},
            )
            info["path"] = shot.folder
            info["count"] = int(info["count"]) + 1
            ann = self.annotations.get(shot.shot_id)
            if ann is not None:
                info["distances"].add(format_distance_label(ann.distance_m))
                info["distance_values"].add(float(ann.distance_m))
                if ann.accepted and ann.reviewed:
                    info["ok"] = int(info["ok"]) + 1
            else:
                info["distances"].add(format_distance_label(shot.distance_m))
                info["distance_values"].add(float(shot.distance_m))

        self._loading = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(folders))
        for row, folder in enumerate(folders):
            group_id = _folder_group_id(folder, self.group_count, self.assignments)
            info = by_folder[folder]
            distances = sorted(info["distances"])
            distance_values = sorted(float(v) for v in info["distance_values"])
            date_text, date_sort = _folder_date_text_and_sort(folder, info.get("path"))
            values = [
                (_group_name(group_id), group_id),
                (folder, folder.lower()),
                (str(info["count"]), int(info["count"])),
                (", ".join(distances[:8]) + (" ..." if len(distances) > 8 else ""), distance_values[0] if distance_values else 0.0),
                (str(info["ok"]), int(info["ok"])),
                (date_text, date_sort),
            ]
            for col, (value, sort_value) in enumerate(values):
                item = self.table.item(row, col)
                if item is None:
                    item = SortTableWidgetItem()
                    self.table.setItem(row, col, item)
                item.setText(value)
                item.setData(Qt.ItemDataRole.UserRole, sort_value)
                item.setForeground(QColor("#eeeeee" if self.dark_mode else "#111111"))
                item.setBackground(QColor("#24272e" if self.dark_mode else "#ffffff"))
        self.table.setSortingEnabled(True)
        self._loading = False
        counts = {
            gid: sum(1 for folder in folders if _folder_group_id(folder, self.group_count, self.assignments) == gid)
            for gid in range(1, self.group_count + 1)
        }
        summary = " | ".join(f"G{gid}: {counts[gid]} carpeta(s)" for gid in range(1, self.group_count + 1))
        self.summary_label.setText(f"{len(folders)} carpetas asignadas | {summary}")

    def set_dark_mode(self, dark: bool) -> None:
        self.dark_mode = bool(dark)
        self._populate_table()


_ALIGN_COLORS = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#46f0f0", "#f032e6", "#bcf60c", "#008080", "#9a6324"]


class AlignmentPanel(QWidget):
    """Tab de enfase: ajuste de offset de tiempo POR CARPETA (tanda/dia) para
    calibrar el desfase entre dias medidos en carpetas distintas antes de
    promediar. El ajuste fino por señal ya no vive aca: se hace poniendo BIEN
    el trigger en Capturas (con el promedio de la carpeta como referencia).

    Flujo: dentro de un label se navega carpeta por carpeta, ordenadas por
    pico a pico del promedio de la carpeta descendente. La primera (la mas
    facil de ver el golpe) define el 0; cada carpeta siguiente se muestra
    como SU PROMEDIO contra los promedios de las carpetas ya confirmadas con
    "OK alineado" (un color por carpeta), y el offset mueve la carpeta
    entera.

    El offset de carpeta se guarda en alignment_offsets[label][carpeta] y es
    el default de todas sus señales (ver `get_alignment_offset` en
    field_review_data.py). Los offsets por señal viejos tienen prioridad, asi
    que al confirmar una carpeta con OK se limpian los de sus señales (el
    trigger de Capturas es ahora el ajuste fino). Offset positivo corre la
    carpeta hacia la izquierda (llegada mas temprana).

    "Rechazar esta carpeta" es una decision independiente de "valida" en
    Capturas: una carpeta puede tener señales con trigger/forma coherentes
    (validas) pero el usuario no confia en como quedo respecto a las demas
    (ej. mal enfasada, ruido raro) y la excluye de promedios/waterfall/MASW/
    export sin tocar sus anotaciones. Se guarda en disabled_folders
    (persistido aparte, `is_folder_disabled` en field_review_data.py)."""

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
        disabled: dict[str, list[str]] | None = None,
        group_count: int = 1,
        group_assignments: dict[str, int] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.dataset = dataset
        self.annotations = annotations
        self.offsets = offsets
        self.shot_offsets = shot_offsets
        self.disabled = disabled if disabled is not None else {}
        self.get_zeroed = get_zeroed
        self.get_peak_to_peak = get_peak_to_peak
        self.dark_mode = dark_mode
        self.on_changed = on_changed
        self.group_count = max(1, int(group_count or 1))
        self.group_assignments = group_assignments if group_assignments is not None else {}
        self._loading = False
        self._saved_group_id: int | None = None
        self._saved_label: str | None = None
        self._saved_folder: str | None = None
        # Carpeta -> (señales, promedio precalculado) del label actual.
        self._folders: list[tuple[str, list[tuple[FieldShot, PickAnnotation]]]] = []
        self._folder_traces: dict[str, tuple[np.ndarray, np.ndarray]] = {}
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
        group_box = QHBoxLayout()
        group_box.addWidget(QLabel("Grupo"))
        self.group_combo = QComboBox()
        self.group_combo.currentIndexChanged.connect(self._group_changed)
        group_box.addWidget(self.group_combo, stretch=1)
        left_layout.addLayout(group_box)

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

        form_box = QGroupBox("Offset de esta carpeta (mueve todas sus señales)")
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

        self.reject_btn = QPushButton("Rechazar esta carpeta")
        self.reject_btn.setCheckable(True)
        self.reject_btn.setToolTip(
            "Excluye esta carpeta de promedios/waterfall/MASW/export aunque sus señales\n"
            "sean validas (trigger y forma coherentes en Capturas): es una decision\n"
            "aparte, ej. porque no confia en como quedo enfasada contra las demas.\n"
            "Las muestras individuales se siguen exportando; solo no entran al promedio."
        )
        self.reject_btn.toggled.connect(self._toggle_reject)
        left_layout.addWidget(self.reject_btn)

        reset_box = QHBoxLayout()
        self.reset_shot_btn = QPushButton("Reset esta carpeta")
        self.reset_shot_btn.clicked.connect(self._reset_folder)
        self.reset_label_btn = QPushButton("Reset todo el label")
        self.reset_label_btn.clicked.connect(self._reset_label)
        reset_box.addWidget(self.reset_shot_btn)
        reset_box.addWidget(self.reset_label_btn)
        left_layout.addLayout(reset_box)

        hint = QLabel(
            "Se trabaja POR CARPETA (tanda/dia), no por señal: el grafico "
            "muestra el PROMEDIO de cada carpeta. Orden: mayor pico a pico "
            "del promedio primero; esa carpeta define el 0. Las ya "
            "confirmadas con 'OK alineado' quedan de referencia (un color "
            "por carpeta) y el offset mueve la carpeta actual entera. Al "
            "confirmar se limpian los offsets por señal viejos de esa "
            "carpeta (el ajuste fino ahora es el trigger en Capturas). "
            "'Rechazar esta carpeta' la saca de promedios/waterfall/MASW/export "
            "aunque sus señales sean validas — es tu decision, no la del trigger. "
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
        self._refresh_group_combo()
        current = self._saved_label or self.label_combo.currentText()
        saved_folder = self._saved_folder
        group_id = self.current_group_id()
        labels = sorted(
            {
                format_distance_label(ann.distance_m)
                for shot in self.dataset.shots
                for ann in (self.annotations.get(shot.shot_id),)
                if (
                    ann is not None
                    and ann.accepted
                    and _folder_group_id(shot.folder_name, self.group_count, self.group_assignments) == group_id
                )
            }
        )
        self._loading = True
        self.label_combo.clear()
        self.label_combo.addItems(labels)
        if current in labels:
            self.label_combo.setCurrentText(current)
        self._loading = False
        self._label_changed()
        if saved_folder:
            for idx, (folder, _pairs) in enumerate(self._folders):
                if folder == saved_folder:
                    self._index = idx
                    self._show_current()
                    break
        self._saved_group_id = None
        self._saved_label = None
        self._saved_folder = None

    def set_grouping(self, group_count: int, assignments: dict[str, int]) -> None:
        self.group_count = max(1, int(group_count or 1))
        self.group_assignments = assignments
        self.refresh()

    def restore_selection(
        self,
        group_id: int | None = None,
        label: str | None = None,
        folder: str | None = None,
    ) -> None:
        self._saved_group_id = int(group_id) if group_id is not None else None
        self._saved_label = str(label) if label else None
        self._saved_folder = str(folder) if folder else None
        self.refresh()

    def selection_state(self) -> dict[str, object | None]:
        current = self._current()
        return {
            "alignment_group_id": self.current_group_id(),
            "alignment_label": self.label_combo.currentText() or None,
            "alignment_folder": current[0] if current is not None else None,
        }

    def current_group_id(self) -> int:
        if not hasattr(self, "group_combo"):
            return 1
        data = self.group_combo.currentData()
        try:
            group_id = int(data)
        except (TypeError, ValueError):
            group_id = 1
        return int(np.clip(group_id, 1, self.group_count))

    def _refresh_group_combo(self) -> None:
        if not hasattr(self, "group_combo"):
            return
        current = self._saved_group_id or self.current_group_id()
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        for gid in range(1, self.group_count + 1):
            n_folders = len({
                shot.folder_name for shot in self.dataset.shots
                if _folder_group_id(shot.folder_name, self.group_count, self.group_assignments) == gid
            })
            self.group_combo.addItem(f"{_group_name(gid)} ({n_folders} carpetas)", gid)
        idx = max(0, min(current - 1, self.group_combo.count() - 1))
        self.group_combo.setCurrentIndex(idx)
        self.group_combo.blockSignals(False)

    def _group_changed(self, *_args) -> None:
        if self._loading:
            return
        self.refresh()

    def _folder_average(
        self, pairs: list[tuple[FieldShot, PickAnnotation]]
    ) -> tuple[np.ndarray, np.ndarray] | None:
        """Promedio del geofono de una carpeta, alineado por trigger de cada
        señal (SIN offsets: la carpeta entera se corre despues, rigida, con
        su offset). Interpola a una grilla comun (maneja fs mezcladas);
        devuelve (tiempo relativo al trigger, media) o None."""
        traces: list[tuple[np.ndarray, np.ndarray]] = []
        for shot, ann in pairs:
            fs = float(shot.fs or shot.geo.fs or shot.hammer.fs)
            if fs <= 0:
                continue
            try:
                _hammer, geo = self.get_zeroed(shot, ann)
            except Exception:
                continue
            if geo.size == 0:
                continue
            t = np.arange(geo.size, dtype=np.float64) / fs - float(ann.trigger_s)
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
        return grid, mean

    def _ordered_folders_for_label(
        self, label: str
    ) -> list[tuple[str, list[tuple[FieldShot, PickAnnotation]]]]:
        """Carpetas del label con sus señales aceptadas, ordenadas por pico a
        pico del PROMEDIO de la carpeta descendente: se empieza por la mas
        facil de ver el golpe, que define el 0. Precalcula los promedios en
        self._folder_traces."""
        by_folder: dict[str, list[tuple[FieldShot, PickAnnotation]]] = {}
        group_id = self.current_group_id()
        for shot in self.dataset.shots:
            ann = self.annotations.get(shot.shot_id)
            if ann is None or not ann.accepted:
                continue
            if _folder_group_id(shot.folder_name, self.group_count, self.group_assignments) != group_id:
                continue
            if format_distance_label(ann.distance_m) != label:
                continue
            by_folder.setdefault(shot.folder_name, []).append((shot, ann))
        self._folder_traces = {}
        p2p: dict[str, float] = {}
        for folder, pairs in by_folder.items():
            result = self._folder_average(pairs)
            if result is None:
                continue
            self._folder_traces[folder] = result
            p2p[folder] = peak_to_peak(result[1])
        return sorted(
            ((folder, by_folder[folder]) for folder in self._folder_traces),
            key=lambda item: -p2p[item[0]],
        )

    def _is_accumulated(self, folder: str) -> bool:
        label = self.label_combo.currentText()
        return folder in self.offsets.get(label, {})

    def _disabled_label_key(self, label: str | None = None) -> str:
        label = label if label is not None else self.label_combo.currentText()
        if self.group_count <= 1:
            return label
        return _group_disabled_key(label, self.current_group_id())

    def _disabled_keys_for_label(self, label: str | None = None) -> set[str]:
        label = label if label is not None else self.label_combo.currentText()
        keys = {self._disabled_label_key(label)}
        if self.group_count > 1 and self.current_group_id() == 1:
            keys.add(label)
        return keys

    def _all_disabled_keys_for_label(self, label: str) -> set[str]:
        keys = {label}
        if self.group_count > 1:
            keys.update(_group_disabled_key(label, gid) for gid in range(1, self.group_count + 1))
        return keys

    def _remove_rejection(self, label: str, folder: str) -> None:
        for disabled_key in self._all_disabled_keys_for_label(label):
            entry = self.disabled.get(disabled_key)
            if entry and folder in entry:
                entry.remove(folder)

    def _set_rejection(self, label: str, folder: str) -> None:
        disabled_key = self._disabled_label_key(label)
        entry = self.disabled.setdefault(disabled_key, [])
        if folder not in entry:
            entry.append(folder)

    def _is_rejected(self, folder: str) -> bool:
        return any(folder in self.disabled.get(key, ()) for key in self._disabled_keys_for_label())

    def _folder_default_offset_s(self, folder: str) -> float:
        label = self.label_combo.currentText()
        return float(self.offsets.get(label, {}).get(folder, 0.0))

    def _current(self) -> tuple[str, list[tuple[FieldShot, PickAnnotation]]] | None:
        if not (0 <= self._index < len(self._folders)):
            return None
        return self._folders[self._index]

    def _label_changed(self, *_args) -> None:
        if self._loading:
            return
        label = self.label_combo.currentText()
        self._folders = self._ordered_folders_for_label(label) if label else []
        self._index = 0
        self._show_current()

    def _show_current(self) -> None:
        current = self._current()
        self._loading = True
        if current is None:
            self.shot_label.setText("-")
            self.offset_spin.setValue(0.0)
            self.reject_btn.blockSignals(True)
            self.reject_btn.setChecked(False)
            self.reject_btn.blockSignals(False)
        else:
            folder, pairs = current
            n_done = sum(1 for f, _p in self._folders if self._is_accumulated(f))
            rejected = self._is_rejected(folder)
            if rejected:
                estado = "RECHAZADA — no entra a promedios/waterfall/MASW/export"
            elif self._is_accumulated(folder):
                estado = "ya alineada"
            elif self._index == 0 and n_done == 0:
                estado = "esta carpeta define el 0 (confirmala con OK)"
            else:
                estado = "sin alinear todavia"
            legacy = sum(1 for shot, _ann in pairs if shot.shot_id in self.shot_offsets)
            legacy_txt = f" — {legacy} offset(s) por señal viejos (OK los limpia)" if legacy else ""
            self.shot_label.setText(
                f"carpeta {self._index + 1}/{len(self._folders)} — {folder} ({len(pairs)} señales)\n"
                f"{estado} — {n_done}/{len(self._folders)} carpetas acumuladas{legacy_txt}"
            )
            self.offset_spin.setValue(self._folder_default_offset_s(folder) * 1000.0)
            self.reject_btn.blockSignals(True)
            self.reject_btn.setChecked(rejected)
            self.reject_btn.blockSignals(False)
        self._loading = False
        self._redraw()

    def _move(self, delta: int) -> None:
        if not self._folders:
            return
        self._index = int(np.clip(self._index + delta, 0, len(self._folders) - 1))
        self._show_current()

    def _advance_after_decision(self) -> None:
        if self._folders and self._index + 1 < len(self._folders):
            self._index += 1
            self._show_current()
            return

        start_group = max(0, self.group_combo.currentIndex())
        start_label = self.label_combo.currentIndex()
        for group_idx in range(start_group, self.group_combo.count()):
            if group_idx != start_group:
                self.group_combo.setCurrentIndex(group_idx)
                start_label = -1
            for label_idx in range(start_label + 1, self.label_combo.count()):
                self.label_combo.setCurrentIndex(label_idx)
                if self._folders:
                    return
            start_label = -1

        self._show_current()
        self.status_label.setText("Enfase terminado: no quedan mas carpetas, labels ni grupos.")

    def _offset_changed(self, _value_ms: float) -> None:
        if self._loading:
            return
        self._redraw()

    def _mark_ok(self) -> None:
        current = self._current()
        if current is None:
            return
        folder, pairs = current
        label = self.label_combo.currentText()
        self._remove_rejection(label, folder)
        self.offsets.setdefault(label, {})[folder] = float(self.offset_spin.value()) / 1000.0
        # Los offsets por señal viejos tienen prioridad sobre el de carpeta y
        # pelearian con este ajuste: se limpian (el ajuste fino ahora es el
        # trigger en Capturas).
        for shot, _ann in pairs:
            self.shot_offsets.pop(shot.shot_id, None)
        if self.on_changed is not None:
            self.on_changed()
        self._advance_after_decision()

    def _toggle_reject(self, checked: bool) -> None:
        if self._loading:
            return
        current = self._current()
        if current is None:
            return
        folder, pairs = current
        label = self.label_combo.currentText()
        if checked:
            self.offsets.get(label, {}).pop(folder, None)
            for shot, _ann in pairs:
                self.shot_offsets.pop(shot.shot_id, None)
            self._set_rejection(label, folder)
        else:
            self._remove_rejection(label, folder)
        if self.on_changed is not None:
            self.on_changed()
        self._advance_after_decision()

    def _reset_folder(self) -> None:
        current = self._current()
        if current is None:
            return
        folder, _pairs = current
        label = self.label_combo.currentText()
        self.offsets.get(label, {}).pop(folder, None)
        self._remove_rejection(label, folder)
        if self.on_changed is not None:
            self.on_changed()
        self._show_current()

    def _reset_label(self) -> None:
        label = self.label_combo.currentText()
        if label in self.offsets:
            self.offsets[label] = {}
        for disabled_key in self._disabled_keys_for_label(label):
            if disabled_key in self.disabled:
                self.disabled[disabled_key] = []
        for _folder, pairs in self._folders:
            for shot, _ann in pairs:
                self.shot_offsets.pop(shot.shot_id, None)
        if self.on_changed is not None:
            self.on_changed()
        self._show_current()

    _MEAN_COLOR = "#2ca02c"

    def _current_highlight_color(self) -> str:
        return "#ffffff" if self.dark_mode else "#000000"

    def _redraw(self) -> None:
        self.plot.clear()
        label = self.label_combo.currentText()
        current = self._current()
        current_folder = current[0] if current is not None else None
        # Promedios de las carpetas ya confirmadas (un color por carpeta),
        # cada uno corrido por su offset guardado — son la referencia.
        legend = self.plot.plotItem.legend
        if legend is None:
            legend = self.plot.addLegend()
        else:
            legend.clear()
        n_acc = 0
        for folder, _pairs in self._folders:
            if folder == current_folder or not self._is_accumulated(folder):
                continue
            trace = self._folder_traces.get(folder)
            if trace is None:
                continue
            grid, mean = trace
            offset_s = self._folder_default_offset_s(folder)
            color = _ALIGN_COLORS[n_acc % len(_ALIGN_COLORS)]
            rejected = self._is_rejected(folder)
            pen = pg.mkPen(color, width=1.6, style=Qt.PenStyle.DashLine if rejected else Qt.PenStyle.SolidLine)
            name = f"{folder} (rechazada)" if rejected else folder
            self.plot.plot(grid - offset_s, mean, pen=pen, name=name)
            n_acc += 1
        # Promedio de la carpeta actual (con el offset en vivo del spin) para
        # moverla entera contra las referencias.
        if current_folder is not None:
            trace = self._folder_traces.get(current_folder)
            if trace is not None:
                grid, mean = trace
                offset_s = float(self.offset_spin.value()) / 1000.0
                self.plot.plot(
                    grid - offset_s,
                    mean,
                    pen=pg.mkPen(self._current_highlight_color(), width=2.4),
                    name=f"{current_folder} (actual)",
                )
        self.plot.setXRange(-0.1, 1.0, padding=0.02)
        if not label:
            self.status_label.setText("Elegi un label para empezar a alinear")
        elif n_acc == 0:
            self.status_label.setText(
                f"{label}: sin carpetas de referencia todavia — esta define el 0, confirmala con OK."
            )
        else:
            self.status_label.setText(
                f"{label}: promedios de {n_acc} carpeta(s) ya alineadas (un color cada una) + "
                f"promedio de la carpeta actual ({'blanco' if self.dark_mode else 'negro'}). "
                "Mové el offset para calzarla."
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
        disabled_folders: dict[str, list[str]] | None = None,
        group_count: int = 1,
        group_assignments: dict[str, int] | None = None,
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
        self.disabled_folders = disabled_folders
        self.group_count = max(1, int(group_count or 1))
        self.group_assignments = group_assignments if group_assignments is not None else {}
        self.arrivals_path = default_average_arrivals_path(self.output_dir)
        self.arrivals = load_average_arrivals(self.arrivals_path)
        self.averages: list[dict] = []
        self.hammer_global: dict | None = None
        self._current_row = -1
        self._loading = False
        self.arrival_line: pg.InfiniteLine | None = None
        self._computing = False
        self._last_signature: tuple | None = None
        self._cache_by_group: dict[int, tuple[tuple, list[dict], dict | None]] = {}
        self._saved_group_id: int | None = None

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
        self._refresh_group_combo()
        self._refresh_averages(force=force)

    def show_waterfall(self) -> None:
        """Re-arma y muestra el waterfall con los promedios actuales (mismo
        camino que el boton 'Ver waterfall')."""
        self._show_waterfall()

    def set_dark_mode(self, dark: bool) -> None:
        self.dark_mode = bool(dark)
        self._style_plots()
        for row, avg in enumerate(self.averages):
            self._update_table_row(row, avg)

    def save_arrivals(self) -> None:
        save_average_arrivals(self.arrivals_path, self.arrivals)

    def set_grouping(self, group_count: int, assignments: dict[str, int]) -> None:
        self.group_count = max(1, int(group_count or 1))
        self.group_assignments = assignments
        self._cache_by_group = {}
        self._last_signature = None
        self.refresh(force=False)

    def restore_selection(self, group_id: int | None = None) -> None:
        self._saved_group_id = int(group_id) if group_id is not None else None
        self.refresh(force=False)

    def selection_state(self) -> dict[str, int]:
        return {"average_group_id": self.current_group_id()}

    def current_group_id(self) -> int:
        if not hasattr(self, "group_combo"):
            return 1
        data = self.group_combo.currentData()
        try:
            group_id = int(data)
        except (TypeError, ValueError):
            group_id = 1
        return int(np.clip(group_id, 1, self.group_count))

    def _refresh_group_combo(self) -> None:
        if not hasattr(self, "group_combo"):
            return
        current = self._saved_group_id or self.current_group_id()
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        for gid in range(1, self.group_count + 1):
            n_folders = len({
                shot.folder_name for shot in self.dataset.shots
                if _folder_group_id(shot.folder_name, self.group_count, self.group_assignments) == gid
            })
            self.group_combo.addItem(f"{_group_name(gid)} ({n_folders} carpetas)", gid)
        idx = max(0, min(current - 1, self.group_combo.count() - 1))
        self.group_combo.setCurrentIndex(idx)
        self.group_combo.blockSignals(False)
        self._saved_group_id = None

    def _group_changed(self, *_args) -> None:
        if self._loading:
            return
        self._last_signature = None
        self._refresh_averages(force=False)

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
        group_row = QHBoxLayout()
        group_row.addWidget(QLabel("Grupo"))
        self.group_combo = QComboBox()
        self.group_combo.currentIndexChanged.connect(self._group_changed)
        group_row.addWidget(self.group_combo, stretch=1)
        left_layout.addLayout(group_row)

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
        group_id = self.current_group_id()
        group_dataset = _filtered_dataset_for_group(
            self.dataset, group_id, self.group_count, self.group_assignments
        )
        disabled_for_group = _project_disabled_for_group(self.disabled_folders, group_id, self.group_count)
        signature = (
            annotations_signature(group_dataset, self.annotations),
            filter_settings_signature(self.filter_settings),
            alignment_offsets_signature(self.alignment_offsets),
            alignment_shot_offsets_signature(self.alignment_shot_offsets),
            disabled_folders_signature(disabled_for_group),
            dispersion_groups_signature(self.group_count, self.group_assignments),
            group_id,
        )
        cached = self._cache_by_group.get(group_id)
        if not force and cached is not None and cached[0] == signature:
            self.averages = cached[1]
            self.hammer_global = cached[2]
            self._last_signature = signature
            self._populate_table()
            filt = self.filter_settings
            filter_txt = ""
            if filt is not None and filt.enabled:
                filter_txt = f" | filtro {filt.low_hz:g}-{filt.high_hz:g} Hz o{filt.order}"
            self.summary_label.setText(
                f"{_group_name(group_id)}: {len(self.averages)} promedios (sin cambios){filter_txt} | {self.output_dir}"
            )
            if self.averages:
                row = min(max(self._current_row, 0), len(self.averages) - 1)
                self.table.selectRow(row)
                self._select_row(row)
            else:
                self._clear_current_average()
            return
        if not force and self.averages and signature == self._last_signature:
            self.summary_label.setText(
                f"{_group_name(group_id)}: {len(self.averages)} promedios (sin cambios) | {self.output_dir}"
            )
            return
        self._computing = True
        self.refresh_btn.setEnabled(False)
        self.summary_label.setText(f"Calculando promedios de {_group_name(group_id)}...")
        self.status_label.setText(f"Calculando promedios de {_group_name(group_id)}, un momento...")
        QApplication.processEvents()
        try:
            groups, hammer_global = compute_average_groups(
                group_dataset,
                self.annotations,
                prefer_filtered=self.prefer_filtered,
                filter_settings=self.filter_settings,
                alignment_offsets=self.alignment_offsets,
                alignment_shot_offsets=self.alignment_shot_offsets,
                disabled_folders=disabled_for_group,
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
        self._cache_by_group[group_id] = (signature, self.averages, self.hammer_global)
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
        self.summary_label.setText(
            f"{_group_name(group_id)}: {len(self.averages)} promedios{filter_txt} | {self.output_dir}"
        )
        if self.averages:
            row = min(max(self._current_row, 0), len(self.averages) - 1)
            self.table.selectRow(row)
            self._select_row(row)
        else:
            self._clear_current_average()
        self._computing = False
        self.refresh_btn.setEnabled(True)

    def _clear_current_average(self) -> None:
        self._current_row = -1
        self.label_value.setText("-")
        self.arrival_value.setText("-")
        self.notes_edit.setText("")
        self.hammer_plot.clear()
        self.geo_plot.clear()
        self._style_plots()
        self.status_label.setText("No hay promedios para el grupo seleccionado.")

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
            group_id = self.current_group_id()
            group_dataset = _filtered_dataset_for_group(
                self.dataset, group_id, self.group_count, self.group_assignments
            )
            result = export_processed(
                group_dataset,
                self.annotations,
                self.output_dir,
                prefer_filtered=self.prefer_filtered,
                average_arrivals=self.arrivals,
                filter_settings=self.filter_settings,
                alignment_offsets=self.alignment_offsets,
                alignment_shot_offsets=self.alignment_shot_offsets,
                disabled_folders=_project_disabled_for_group(self.disabled_folders, group_id, self.group_count),
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
            group_id = self.current_group_id()
            self.on_show_waterfall(
                common_time,
                distances,
                matrix,
                self.arrivals,
                self.hammer_global,
                len(self.averages),
                group_id,
                _group_name(group_id),
            )
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
    (ver nota en HANDOFF_FIELD_REVIEW.md, Fase 8).

    Excepcion a lo anterior: 'Invertir traza' y 'Auto polaridad' NO son de
    vista — togglean geo_flip en las anotaciones (a nivel captura), asi que
    persisten y afectan promedios, MASW y export."""

    def __init__(
        self,
        dark_mode: bool = False,
        on_show_masw=None,
        on_auto_masw=None,
        on_flip_distance=None,
        on_auto_polarity=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.dark_mode = dark_mode
        self.on_show_masw = on_show_masw
        self.on_auto_masw = on_auto_masw
        self.on_flip_distance = on_flip_distance
        self.on_auto_polarity = on_auto_polarity
        self._crosshair_v: pg.InfiniteLine | None = None
        self._crosshair_h: pg.InfiniteLine | None = None
        self._proxy = None
        self._last_data: dict | None = None
        self._data_by_group: dict[int, dict] = {}
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
        top.addWidget(QLabel("Grupo"))
        self.group_combo = QComboBox()
        self.group_combo.currentIndexChanged.connect(self._waterfall_group_changed)
        top.addWidget(self.group_combo)
        self.raw_amplitude_check = QCheckBox("Amplitud real (ver atenuacion)")
        self.raw_amplitude_check.setToolTip(
            "Por defecto cada traza se normaliza a su propio pico para comparar formas.\n"
            "Con esto activado todas comparten la misma escala, asi se ve como cae la amplitud con la distancia."
        )
        self.raw_amplitude_check.toggled.connect(self._redraw)
        top.addWidget(self.raw_amplitude_check)
        top.addWidget(QLabel("Filtro K"))
        self.kfilter_combo = QComboBox()
        self.kfilter_combo.addItems(["Off", "Directo", "Inverso"])
        self.kfilter_combo.setToolTip(
            "Filtro direccional frecuencia-numero de onda (f-k).\n"
            "Separa las ondas por direccion de propagacion. El sentido 'correcto' depende de\n"
            "como quedo el tendido; como no esta bien definido, elegi el que deje la onda\n"
            "principal (directa) y no el rebote:\n"
            " - Directo: conserva moveout positivo (fuente→geofonos, k·f ≤ 0).\n"
            " - Inverso: conserva la mitad opuesta (rebotes / sentido contrario).\n"
            "Se aplica a la vista y a lo que se manda a MASW; el export no se toca."
        )
        self.kfilter_combo.currentIndexChanged.connect(self._redraw)
        top.addWidget(self.kfilter_combo)
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
        self.auto_polarity_btn = QPushButton("Auto polaridad")
        self.auto_polarity_btn.setToolTip(
            "Corrige la polaridad del geofono en dos etapas:\n"
            " 1) Intra-punto: las capturas SIN validar se enfasan contra el consenso de las\n"
            "    validadas de su distancia (las validadas no se tocan; el flip queda como\n"
            "    propuesta que aceptas al revisarlas en Capturas).\n"
            " 2) Inter-punto: cada promedio se correlaciona con el del punto vecino ya\n"
            "    alineado; si da en contrafase se invierte el punto COMPLETO (geo_flip).\n"
            "Persiste en las anotaciones y afecta promedios, MASW y export.\n"
            "(No confundir con 'Auto inversion', que es la inversion MASW.)"
        )
        self.auto_polarity_btn.clicked.connect(self._request_auto_polarity)
        top.addWidget(self.auto_polarity_btn)
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
        self.flip_trace_btn = QPushButton("Invertir traza")
        self.flip_trace_btn.setToolTip(
            "Invierte la polaridad de la traza seleccionada (apretar de nuevo la devuelve).\n"
            "No es solo de vista: togglea geo_flip en TODAS las capturas de esa distancia,\n"
            "asi el cambio persiste y llega a promedios, MASW y export."
        )
        self.flip_trace_btn.clicked.connect(self._flip_selected_trace)
        traces_layout.addWidget(self.flip_trace_btn)
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

    def _update_group_combo(self, select_group: int | None = None) -> None:
        if not hasattr(self, "group_combo"):
            return
        current = int(select_group or (self._last_data or {}).get("group_id", 1))
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        for group_id in sorted(self._data_by_group):
            data = self._data_by_group[group_id]
            label = str(data.get("group_name", _group_name(group_id)))
            n = int(data.get("n_averages", 0))
            self.group_combo.addItem(f"{label} ({n} promedios)", group_id)
        idx = self.group_combo.findData(current)
        if idx < 0 and self.group_combo.count():
            idx = 0
        if idx >= 0:
            self.group_combo.setCurrentIndex(idx)
        self.group_combo.blockSignals(False)

    def _waterfall_group_changed(self, _idx: int) -> None:
        data = self.group_combo.currentData()
        if data is None:
            return
        group_id = int(data)
        selected = self._data_by_group.get(group_id)
        if selected is None:
            return
        self._last_data = selected
        self._rebuild_trace_list(selected["distances"])
        self._redraw()

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
        """Filtro f-k direccional sobre TODAS las distancias, antes de descartar
        las ocultas — asi la resolucion en k usa el tendido completo. El sentido
        se elige en el combo (Directo = moveout positivo, Inverso = la mitad
        opuesta) porque cual es el 'correcto' depende del tendido."""
        mode = self.kfilter_combo.currentText() if hasattr(self, "kfilter_combo") else "Off"
        if mode == "Off" or self._last_data is None:
            return matrix
        distances = self._last_data["distances"]
        keep_forward = mode != "Inverso"
        try:
            return np.asarray(
                fk_directional_filter(matrix, distances, common_time, keep_forward=keep_forward),
                dtype=np.float64,
            )
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
        group_id: int = 1,
        group_name: str | None = None,
    ) -> None:
        group_id = int(group_id or 1)
        self._last_data = {
            "common_time": common_time,
            "distances": distances,
            "matrix": matrix,
            "arrivals": arrivals,
            "hammer_global": hammer_global,
            "n_averages": n_averages,
            "group_id": group_id,
            "group_name": group_name or _group_name(group_id),
        }
        self._data_by_group[group_id] = self._last_data
        self._update_group_combo(group_id)
        self._rebuild_trace_list(distances)
        self._redraw()

    # -------------------------------------------------- persistencia (feat 3)

    def get_state(self) -> dict:
        d = self._last_data
        hammer = d.get("hammer_global") if d else None
        group_states = {}
        for group_id, data in self._data_by_group.items():
            group_hammer = data.get("hammer_global")
            group_states[str(group_id)] = {
                "group_id": int(group_id),
                "group_name": str(data.get("group_name", _group_name(group_id))),
                "n_averages": int(data.get("n_averages", 0)),
                "hammer_n": (group_hammer.get("n") if isinstance(group_hammer, dict) else None),
                "hammer_inverted": (
                    bool(group_hammer.get("inverted")) if isinstance(group_hammer, dict) else None
                ),
            }
        return {
            "hidden_distances": [float(v) for v in sorted(self._hidden_distances)],
            "trim_enabled": bool(self.trim_enabled_check.isChecked()),
            "trim_start": float(self.trim_start_spin.value()),
            "trim_end": float(self.trim_end_spin.value()),
            "raw_amplitude": bool(self.raw_amplitude_check.isChecked()),
            "kfilter_mode": self.kfilter_combo.currentText(),
            "n_averages": int(d["n_averages"]) if d else 0,
            "group_id": int(d.get("group_id", 1)) if d else 1,
            "group_name": str(d.get("group_name", "Grupo 1")) if d else "Grupo 1",
            "active_group": int(d.get("group_id", 1)) if d else 1,
            "groups": group_states,
            "has_data": d is not None,
            "hammer_n": (hammer.get("n") if isinstance(hammer, dict) else None),
            "hammer_inverted": (bool(hammer.get("inverted")) if isinstance(hammer, dict) else None),
        }

    def get_arrays(self) -> dict:
        if not self._data_by_group and self._last_data is None:
            return {}
        arrays = {}
        if self._data_by_group:
            group_ids = np.array(sorted(self._data_by_group), dtype=np.int32)
            arrays["wf_group_ids"] = group_ids
            for raw_gid in group_ids:
                group_id = int(raw_gid)
                data = self._data_by_group[group_id]
                prefix = f"wf_g{group_id}"
                arrays[f"{prefix}_common_time"] = np.asarray(data["common_time"], dtype=np.float64)
                arrays[f"{prefix}_distances"] = np.asarray(data["distances"], dtype=np.float64)
                arrays[f"{prefix}_matrix"] = np.asarray(data["matrix"], dtype=np.float64)
                hammer = data.get("hammer_global")
                if isinstance(hammer, dict):
                    try:
                        ht, hm = hammer_global_time_signal(hammer)
                        arrays[f"{prefix}_hammer_time"] = np.asarray(ht, dtype=np.float64)
                        arrays[f"{prefix}_hammer_mean"] = np.asarray(hm, dtype=np.float64)
                    except Exception:
                        pass
        d = self._last_data
        if d is None:
            return arrays
        arrays["wf_common_time"] = np.asarray(d["common_time"], dtype=np.float64)
        arrays["wf_distances"] = np.asarray(d["distances"], dtype=np.float64)
        arrays["wf_matrix"] = np.asarray(d["matrix"], dtype=np.float64)
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
        ):
            widget.blockSignals(True)
            widget.setChecked(bool(state.get(key, False)))
            widget.blockSignals(False)
        kmode = state.get("kfilter_mode")
        if kmode is None:  # compat con el bool viejo
            kmode = "Directo" if state.get("kfilter") else "Off"
        self.kfilter_combo.blockSignals(True)
        idx = self.kfilter_combo.findText(str(kmode))
        self.kfilter_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.kfilter_combo.blockSignals(False)
        for spin, key in ((self.trim_start_spin, "trim_start"), (self.trim_end_spin, "trim_end")):
            if key in state:
                spin.blockSignals(True)
                spin.setValue(float(state[key]))
                spin.blockSignals(False)
        groups_state = state.get("groups", {}) if isinstance(state.get("groups"), dict) else {}
        if "wf_group_ids" in arrays:
            self._data_by_group = {}
            for raw_gid in np.asarray(arrays["wf_group_ids"]).astype(int):
                gid = int(raw_gid)
                prefix = f"wf_g{gid}"
                if f"{prefix}_matrix" not in arrays:
                    continue
                meta = groups_state.get(str(gid), {}) if isinstance(groups_state, dict) else {}
                hammer_global = None
                if f"{prefix}_hammer_time" in arrays and f"{prefix}_hammer_mean" in arrays:
                    hammer_global = {
                        "time_s": np.asarray(arrays[f"{prefix}_hammer_time"], dtype=np.float64),
                        "hammer_mean_v": np.asarray(arrays[f"{prefix}_hammer_mean"], dtype=np.float64),
                        "n": meta.get("hammer_n"),
                        "inverted": bool(meta.get("hammer_inverted")),
                    }
                self._data_by_group[gid] = {
                    "common_time": np.asarray(arrays[f"{prefix}_common_time"], dtype=np.float64),
                    "distances": [float(d) for d in arrays[f"{prefix}_distances"]],
                    "matrix": np.asarray(arrays[f"{prefix}_matrix"], dtype=np.float64),
                    "arrivals": arrivals,
                    "hammer_global": hammer_global,
                    "n_averages": int(meta.get("n_averages", 0)),
                    "group_id": gid,
                    "group_name": str(meta.get("group_name", _group_name(gid))),
                }
            if self._data_by_group:
                active_group = int(state.get("active_group", state.get("group_id", min(self._data_by_group))))
                self._last_data = self._data_by_group.get(active_group) or self._data_by_group[min(self._data_by_group)]
                self._update_group_combo(int(self._last_data.get("group_id", 1)))
                self._rebuild_trace_list(self._last_data["distances"])
                self._redraw()
                return
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
            int(state.get("group_id", 1)),
            str(state.get("group_name", _group_name(int(state.get("group_id", 1))))),
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
        group_name = self._last_data.get("group_name", "Grupo 1")
        raw_amplitude = self.raw_amplitude_check.isChecked()

        self.plot.clear()
        self.plot.addItem(self._crosshair_v, ignoreBounds=True)
        self.plot.addItem(self._crosshair_h, ignoreBounds=True)
        self._crosshair_v.hide()
        self._crosshair_h.hide()

        if common_time.size == 0 or not distances:
            self.info_label.setText(
                f"{group_name}: {n_averages} promedios | sin trazas visibles (recorte o filtro vacio)"
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
        self.info_label.setText(f"{group_name}: {n_averages} promedios{extra} | escala: {mode}")

    def _flip_selected_trace(self) -> None:
        if self._last_data is None:
            self.info_label.setText("Todavia no hay waterfall calculado.")
            return
        item = self.trace_list.currentItem()
        if item is None:
            self.info_label.setText("Selecciona una traza en la lista para invertirla.")
            return
        if self.on_flip_distance is not None:
            self.on_flip_distance(float(item.data(Qt.ItemDataRole.UserRole)))

    def _request_auto_polarity(self) -> None:
        if self.on_auto_polarity is not None:
            self.on_auto_polarity()

    def flip_row(self, distance_m: float) -> bool:
        """Niega en memoria la fila del waterfall de esa distancia y redibuja.
        Como el promedio es lineal, esto es exactamente lo mismo que
        recalcular los promedios con el geo_flip ya toggleado."""
        if self._last_data is None:
            return False
        key = round(float(distance_m), 6)
        distances = self._last_data["distances"]
        matrix = self._last_data["matrix"]
        for i, distance in enumerate(distances):
            if round(float(distance), 6) == key:
                matrix[i, :] = -matrix[i, :]
                self._redraw()
                return True
        return False

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
        group_id = int(self._last_data.get("group_id", 1))
        group_name = str(self._last_data.get("group_name", _group_name(group_id)))
        callback(common_time, distances, matrix, group_id, group_name)


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
    experimental por modo. Con maswavespy la inversion corre sobre el modo
    activo (selector); evodcinv/disba, disba+MC propio y ADsurf (AD/PyTorch,
    ver masw_adsurf.py y third-party/ADsurf) hacen inversion conjunta
    multimodo con TODAS las curvas."""

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
        self._raw_groups: dict[int, dict[str, object]] = {}
        self._active_data_group_id = 1
        self._group_weights: dict[int, float] = {}
        self._group_results: dict[int, dict[str, object]] = {}
        self._weight_slider_max = 2.0
        self._weight_sliders: dict[int, QSlider] = {}
        self._weight_value_labels: dict[int, QLabel] = {}
        self._freq_log_scale = False
        self._intensity_log_scale = False
        # Si esta activo, la imagen mostrada normaliza cada FRECUENCIA por su
        # propio maximo (In(f,c) = I(f,c)/max_c I(f,c)) en vez de por un unico
        # maximo global. Resalta a que velocidad viajo cada frecuencia (util
        # para ver modos superiores) sin cambiar los picks: argmax por fila es
        # invariante a un reescalado positivo, sea global o por fila.
        self._intensity_per_freq = False
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
        self._refresh_mode_combo()
        self._refresh_mode_legend()
        self._backend_changed()

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
        self.clear_group_data_btn = QPushButton("Limpiar grupos MASW")
        self.clear_group_data_btn.setToolTip("Borra los waterfalls cargados en MASW para empezar una combinacion nueva.")
        self.clear_group_data_btn.clicked.connect(lambda: self.clear_group_data())
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
        controls.addWidget(self.clear_group_data_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.weight_box = QGroupBox("Pesos para combinar imagenes normalizadas")
        self.weight_layout = QVBoxLayout(self.weight_box)
        self.weight_hint_label = QLabel(
            "Carga waterfalls desde cada grupo. La imagen combinada usa Icomb(f,c)=sum(wg * Ig(f,c)/max_c Ig(f,c))."
        )
        self.weight_hint_label.setWordWrap(True)
        self.weight_layout.addWidget(self.weight_hint_label)
        weight_limit_row = QHBoxLayout()
        weight_limit_row.addWidget(QLabel("Max peso"))
        self.weight_max_spin = QDoubleSpinBox()
        self.weight_max_spin.setRange(0.01, 1000.0)
        self.weight_max_spin.setDecimals(2)
        self.weight_max_spin.setSingleStep(0.25)
        self.weight_max_spin.setValue(self._weight_slider_max)
        self.weight_max_spin.setToolTip(
            "Fija el maximo de las barras de peso. La imagen combinada se renormaliza "
            "despues de sumar y la escala de color queda siempre en 0..1."
        )
        self.weight_max_spin.valueChanged.connect(self._weight_max_changed)
        weight_limit_row.addWidget(self.weight_max_spin)
        weight_limit_row.addStretch(1)
        self.weight_layout.addLayout(weight_limit_row)
        layout.addWidget(self.weight_box)

        display_row = QHBoxLayout()
        display_row.addWidget(QLabel("Vista"))
        self.freq_log_btn = QPushButton("Eje f log")
        self.freq_log_btn.setCheckable(True)
        self.freq_log_btn.setToolTip(
            "Alterna el eje horizontal de frecuencia entre escala lineal y log10. "
            "Los picks y regiones siguen guardandose en Hz."
        )
        self.freq_log_btn.toggled.connect(self._freq_log_changed)
        self.intensity_per_freq_btn = QPushButton("Intensidad por frecuencia")
        self.intensity_per_freq_btn.setCheckable(True)
        self.intensity_per_freq_btn.setToolTip(
            "Normaliza cada frecuencia por su propio maximo (In(f,c)=I(f,c)/max_c I(f,c)) en vez "
            "de un unico maximo global. Resalta a que velocidad viajo cada frecuencia -- util para "
            "distinguir el modo fundamental de los superiores. No cambia los picks (el argmax por "
            "frecuencia es el mismo con cualquiera de las dos normalizaciones), solo el contraste."
        )
        self.intensity_per_freq_btn.toggled.connect(self._intensity_per_freq_changed)
        self.intensity_log_btn = QPushButton("Intensidad log")
        self.intensity_log_btn.setCheckable(True)
        self.intensity_log_btn.setToolTip(
            "Muestra la intensidad con una transformacion logaritmica visual. "
            "No cambia los picks ni el calculo; solo levanta energia debil."
        )
        self.intensity_log_btn.toggled.connect(self._intensity_log_changed)
        display_row.addWidget(self.freq_log_btn)
        display_row.addWidget(self.intensity_per_freq_btn)
        display_row.addWidget(self.intensity_log_btn)
        display_row.addStretch(1)
        layout.addLayout(display_row)

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
        try:
            self._plot_item.vb.setAspectLocked(False)
        except Exception:
            pass
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

        engine_box = QGroupBox("Motor de inversión")
        engine_layout = QVBoxLayout(engine_box)
        self.backend_combo = QComboBox()
        for key, label, _kind in masw_backends.BACKENDS:
            self.backend_combo.addItem(label, key)
        self.backend_combo.setToolTip(
            "Herramienta con la que se invierte. Los 'inproc' (evodcinv, disba+MC, "
            "maswavespy, ADsurf) corren dentro de la app; Geopsy/Dinver es la unica "
            "externa (exporta las curvas y, si esta en el PATH, se lanza). maswavespy "
            "usa el port numpy (1 modo); evodcinv, disba+MC y ADsurf usan TODAS las "
            "curvas de modo."
        )
        self.backend_combo.currentIndexChanged.connect(self._backend_changed)
        engine_layout.addWidget(self.backend_combo)
        self.backend_status_label = QLabel("")
        self.backend_status_label.setWordWrap(True)
        engine_layout.addWidget(self.backend_status_label)
        left_layout.addWidget(engine_box)

        self.run_inv_btn = QPushButton("Correr inversión")
        self.run_inv_btn.setToolTip(
            "Corre el motor seleccionado con las curvas pickeadas (todas las de modo si el motor "
            "es multimodo). Para motores externos, exporta/lanza."
        )
        self.run_inv_btn.clicked.connect(self._run_selected_inversion)
        self.stop_inv_btn = QPushButton("Detener")
        self.stop_inv_btn.setEnabled(False)
        self.stop_inv_btn.clicked.connect(self._stop_inversion)
        left_layout.addWidget(self.run_inv_btn)
        left_layout.addWidget(self.stop_inv_btn)

        self.export_curves_btn = QPushButton("Exportar curvas (CSV)")
        self.export_curves_btn.setToolTip("Guarda las curvas de todos los modos como CSV para revisarlas o cargarlas en otra herramienta.")
        self.export_curves_btn.clicked.connect(lambda: self._export_to_tool("generic"))
        left_layout.addWidget(self.export_curves_btn)

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
        self.clear_group_data(reset_message=False)
        self.set_group_data(common_time, distances, matrix, group_id=1, group_name="Grupo 1")

    def set_group_data(
        self,
        common_time: np.ndarray,
        distances: list[float],
        matrix: np.ndarray,
        group_id: int = 1,
        group_name: str | None = None,
    ) -> None:
        group_id = max(1, int(group_id or 1))
        group_name = group_name or _group_name(group_id)
        t_trim, m_trim = common_finite_window(common_time, matrix, t_min=0.0)
        if t_trim.size < 8:
            QMessageBox.warning(
                self,
                "MASW",
                f"El tramo comun de {group_name} (sin NaN, desde t=0) es demasiado corto para calcular MASW.",
            )
            return
        distances_list = [float(d) for d in distances]
        self._raw_groups[group_id] = {
            "name": group_name,
            "time": np.asarray(t_trim, dtype=np.float64),
            "distances": distances_list,
            "matrix": np.asarray(m_trim, dtype=np.float64),
            "spacing": self._estimate_spacing_m(distances_list),
            "length": self._estimate_array_length(distances_list),
        }
        self._group_weights.setdefault(group_id, 1.0)
        self._active_data_group_id = group_id
        self._last_result = None
        self._group_results = {}
        self._reset_all_modes()
        self._update_combined_geometry()
        self._update_weight_controls()
        self.inner_tabs.setCurrentIndex(0)
        spacing_txt = f"{self._geophone_spacing_m:.2f} m" if self._geophone_spacing_m else "desconocido"
        length_txt = f"{self._array_length_m:.1f} m" if self._array_length_m else "desconocido"
        loaded = ", ".join(
            f"G{gid}({len(self._raw_groups[gid]['distances'])} canales)" for gid in sorted(self._raw_groups)
        )
        self.info_label.setText(
            f"{group_name} cargado. Grupos en MASW: {loaded}. "
            f"Limites combinados: dx~{spacing_txt}, L~{length_txt}. "
            "Ajusta parametros/pesos y presiona 'Calcular imagen'."
        )

    def clear_group_data(self, reset_message: bool = True) -> None:
        self._raw_groups = {}
        self._group_weights = {}
        self._group_results = {}
        self._weight_sliders = {}
        self._weight_value_labels = {}
        self._raw_time = None
        self._raw_distances = None
        self._raw_matrix = None
        self._last_result = None
        self._geophone_spacing_m = None
        self._array_length_m = None
        self._reset_all_modes()
        if hasattr(self, "weight_layout"):
            self._update_weight_controls()
        if reset_message and hasattr(self, "info_label"):
            self.info_label.setText(
                "Sin datos todavia. Anda a la pestaña Waterfall y presiona 'Ver MASW'."
            )

    def _update_combined_geometry(self) -> None:
        if not self._raw_groups:
            return
        active = self._raw_groups.get(self._active_data_group_id) or next(iter(self._raw_groups.values()))
        self._raw_time = np.asarray(active["time"], dtype=np.float64)
        self._raw_matrix = np.asarray(active["matrix"], dtype=np.float64)
        dist_union = sorted({
            float(d)
            for group in self._raw_groups.values()
            for d in group.get("distances", [])
        })
        self._raw_distances = dist_union if dist_union else [float(d) for d in active["distances"]]
        spacings = [
            float(group["spacing"]) for group in self._raw_groups.values()
            if group.get("spacing") is not None and float(group["spacing"]) > 0
        ]
        lengths = [
            float(group["length"]) for group in self._raw_groups.values()
            if group.get("length") is not None and float(group["length"]) > 0
        ]
        # Para la imagen combinada usamos limites conservadores: el peor dx
        # (mayor aliasing espacial) y la menor apertura L.
        self._geophone_spacing_m = max(spacings) if spacings else self._estimate_spacing_m(self._raw_distances or [])
        self._array_length_m = min(lengths) if lengths else self._estimate_array_length(self._raw_distances or [])

    def _delete_layout_item(self, item) -> None:
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
            return
        layout = item.layout()
        if layout is None:
            return
        while layout.count():
            self._delete_layout_item(layout.takeAt(0))

    def _update_weight_controls(self) -> None:
        if not hasattr(self, "weight_layout"):
            return
        static_rows = 2 if hasattr(self, "weight_max_spin") else 1
        while self.weight_layout.count() > static_rows:
            self._delete_layout_item(self.weight_layout.takeAt(static_rows))
        self._weight_sliders = {}
        self._weight_value_labels = {}
        slider_max = max(1, int(round(float(self._weight_slider_max) * 100.0)))
        for group_id in sorted(self._raw_groups):
            group = self._raw_groups[group_id]
            weight = float(np.clip(self._group_weights.get(group_id, 1.0), 0.0, self._weight_slider_max))
            self._group_weights[group_id] = weight
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(QLabel(str(group.get("name", _group_name(group_id)))))
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, slider_max)
            slider.setSingleStep(5)
            slider.setPageStep(10)
            slider.setValue(int(np.clip(round(weight * 100.0), 0, slider_max)))
            value_label = QLabel(f"{weight:.2f}")
            slider.valueChanged.connect(lambda value, gid=group_id: self._weight_slider_changed(gid, value))
            row.addWidget(slider, stretch=1)
            row.addWidget(value_label)
            self.weight_layout.addWidget(row_widget)
            self._weight_sliders[group_id] = slider
            self._weight_value_labels[group_id] = value_label

    def _weight_max_changed(self, value: float) -> None:
        self._weight_slider_max = max(0.01, float(value))
        self._update_weight_controls()
        if self._group_results:
            self._recombine_group_results(preserve_view=True)
        elif self._last_result is not None:
            f, c, A = self._last_result
            self._display_dispersion_image(f, c, A, self.info_label.text(), preserve_view=True)

    def _weight_slider_changed(self, group_id: int, value: int) -> None:
        weight = float(value) / 100.0
        self._group_weights[int(group_id)] = weight
        label = self._weight_value_labels.get(int(group_id))
        if label is not None:
            label.setText(f"{weight:.2f}")
        if self._group_results:
            self._recombine_group_results(preserve_view=True)

    def _freq_log_changed(self, checked: bool) -> None:
        view_range = self._current_dispersion_view_range()
        self._freq_log_scale = bool(checked)
        self._redraw_last_dispersion(preserve_view=True, view_range=view_range)

    def _intensity_log_changed(self, checked: bool) -> None:
        self._intensity_log_scale = bool(checked)
        self._redraw_last_dispersion(preserve_view=True)

    def _combined_display_array(self, A_full: np.ndarray, A_scale: np.ndarray | None = None) -> np.ndarray:
        """Aplica la normalizacion de color que corresponda segun el toggle
        'Intensidad por frecuencia': por fila (In=I/max_c I, resalta modos) o
        por un unico maximo global (contraste natural entre grupos).

        `A_scale` (si se da) es una version enmascarada (NaN fuera de la
        banda anti-aliasing/lambda_max) que se usa SOLO para calcular el
        maximo de normalizacion, para que el contraste se ajuste a la banda
        valida de picking; el plano que se devuelve (y se grafica) es
        siempre `A_full`, el plano entero -- las curvas de aliasing/apertura
        quedan como sugerencia dibujada encima (`_draw_alias_boundary`,
        `_draw_lambda_max_boundary`), no recortan la imagen."""
        if self._intensity_per_freq:
            return self._normalize_dispersion_image(A_full, A_scale)
        return self._normalize_global_dispersion_image(A_full, A_scale)

    def _intensity_per_freq_changed(self, checked: bool) -> None:
        self._intensity_per_freq = bool(checked)
        self._redraw_last_dispersion(preserve_view=True)

    def _dispersion_level_max(self) -> float:
        return 1.0

    def _freq_to_x_array(self, f: np.ndarray | list[float]) -> np.ndarray:
        arr = np.asarray(f, dtype=np.float64)
        if not self._freq_log_scale:
            return arr
        out = np.full(arr.shape, np.nan, dtype=np.float64)
        valid = arr > 0
        out[valid] = np.log10(arr[valid])
        return out

    def _freq_to_x(self, f_val: float) -> float:
        if not self._freq_log_scale:
            return float(f_val)
        return float(np.log10(max(float(f_val), 1e-12)))

    def _x_to_freq(self, x_val: float) -> float:
        if not self._freq_log_scale:
            return float(x_val)
        x_val = float(x_val)
        if x_val > 12:
            return float("inf")
        if x_val < -12:
            return 0.0
        return float(10.0 ** x_val)

    def _current_dispersion_view_range(self) -> tuple[float, float, float, float] | None:
        if not hasattr(self, "_plot_item"):
            return None
        try:
            (x0, x1), (y0, y1) = self._plot_item.vb.viewRange()
        except Exception:
            return None
        f0, f1 = self._x_to_freq(float(x0)), self._x_to_freq(float(x1))
        if not all(np.isfinite(v) for v in (f0, f1, y0, y1)):
            return None
        f_min, f_max = min(f0, f1), max(f0, f1)
        if self._last_result is not None:
            f = np.asarray(self._last_result[0], dtype=np.float64)
            valid = f[np.isfinite(f) & (f > 0 if self._freq_log_scale else np.ones(f.shape, dtype=bool))]
            if valid.size:
                f_min = max(f_min, float(valid[0]))
                f_max = min(f_max, float(valid[-1]))
                if f_max <= f_min:
                    f_min, f_max = float(valid[0]), float(valid[-1])
        return (f_min, f_max, float(y0), float(y1))

    def _current_raw_dispersion_view_range(self) -> tuple[float, float, float, float] | None:
        if not hasattr(self, "_plot_item"):
            return None
        try:
            (x0, x1), (y0, y1) = self._plot_item.vb.viewRange()
        except Exception:
            return None
        if not all(np.isfinite(v) for v in (x0, x1, y0, y1)):
            return None
        return (float(x0), float(x1), float(y0), float(y1))

    def _restore_raw_dispersion_view_range(self, view_range: tuple[float, float, float, float] | None) -> None:
        if view_range is None:
            return
        x0, x1, y0, y1 = view_range
        if x1 <= x0 or y1 <= y0:
            return
        self._plot_item.setXRange(x0, x1, padding=0.0)
        self._plot_item.setYRange(y0, y1, padding=0.0)

    def _restore_dispersion_view_range(self, view_range: tuple[float, float, float, float] | None) -> None:
        if view_range is None:
            return
        f0, f1, y0, y1 = view_range
        if f1 <= f0 or y1 <= y0:
            return
        x0, x1 = self._freq_to_x(f0), self._freq_to_x(f1)
        if not all(np.isfinite(v) for v in (x0, x1, y0, y1)):
            return
        self._plot_item.setXRange(min(x0, x1), max(x0, x1), padding=0.0)
        self._plot_item.setYRange(y0, y1, padding=0.0)

    def _redraw_last_dispersion(
        self,
        preserve_view: bool = True,
        view_range: tuple[float, float, float, float] | None = None,
    ) -> None:
        if self._last_result is None:
            return
        f, c, A = self._last_result
        self._display_dispersion_image(
            f,
            c,
            A,
            self.info_label.text(),
            preserve_view=preserve_view,
            view_range=view_range,
        )

    def _axis_image_data(self, f: np.ndarray, A: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        f = np.asarray(f, dtype=np.float64)
        A = np.asarray(A, dtype=np.float64)
        if not self._freq_log_scale:
            return f, A
        valid_f = f > 0
        if np.count_nonzero(valid_f) < 2:
            return f, A
        x_src = np.log10(f[valid_f])
        A_src = A[valid_f, :]
        x_grid = np.linspace(float(x_src[0]), float(x_src[-1]), x_src.size)
        out = np.full((x_grid.size, A_src.shape[1]), np.nan, dtype=np.float64)
        for j in range(A_src.shape[1]):
            col = A_src[:, j]
            valid = np.isfinite(col)
            if np.count_nonzero(valid) >= 2:
                out[:, j] = np.interp(x_grid, x_src[valid], col[valid], left=np.nan, right=np.nan)
            elif np.count_nonzero(valid) == 1:
                idx = int(np.flatnonzero(valid)[0])
                out[np.argmin(np.abs(x_grid - x_src[idx])), j] = col[idx]
        return x_grid, out

    def _display_intensity_image(self, A: np.ndarray) -> np.ndarray:
        A = np.asarray(A, dtype=np.float64)
        finite_A = np.where(np.isfinite(A), A, 0.0)
        finite_A = np.clip(finite_A, 0.0, None)
        if not self._intensity_log_scale:
            return finite_A
        gain = 100.0
        return np.log1p(gain * finite_A) / np.log1p(gain)

    @staticmethod
    def _dispersion_colormap() -> pg.ColorMap:
        return pg.ColorMap(
            np.array([0.0, 0.28, 0.48, 0.68, 0.86, 1.0], dtype=np.float64),
            np.array(
                [
                    [0, 0, 130, 255],
                    [0, 90, 255, 255],
                    [0, 190, 90, 255],
                    [255, 230, 0, 255],
                    [255, 0, 0, 255],
                    [90, 0, 0, 255],
                ],
                dtype=np.ubyte,
            ),
        )

    @staticmethod
    def _normalize_dispersion_image(A: np.ndarray, A_scale: np.ndarray | None = None) -> np.ndarray:
        """Normaliza por fila (In = I / max_c I). `A_scale` (si se da) es de
        donde sale el maximo por fila -- se puede pasar una version
        enmascarada (banda valida de picking) para que el contraste se
        calcule ahi, mientras se sigue mostrando/devolviendo `A` completo
        (plano entero, sin recortar por aliasing/lambda_max)."""
        A = np.asarray(A, dtype=np.float64)
        scale_src = np.asarray(A_scale, dtype=np.float64) if A_scale is not None else A
        finite_scale = np.where(np.isfinite(scale_src), scale_src, 0.0)
        denom = np.max(finite_scale, axis=1, keepdims=True) if finite_scale.size else np.array([[0.0]])
        finite_A = np.where(np.isfinite(A), A, 0.0)
        return np.divide(
            finite_A,
            denom,
            out=np.zeros_like(finite_A, dtype=np.float64),
            where=denom > 0,
        )

    @staticmethod
    def _normalize_global_dispersion_image(A: np.ndarray, A_scale: np.ndarray | None = None) -> np.ndarray:
        """Igual que `_normalize_dispersion_image` pero con un unico maximo
        global en vez de uno por fila; `A_scale` opcional para calcular ese
        maximo sobre una banda distinta a la que se devuelve."""
        A = np.asarray(A, dtype=np.float64)
        scale_src = np.asarray(A_scale, dtype=np.float64) if A_scale is not None else A
        finite_scale = np.where(np.isfinite(scale_src), scale_src, 0.0)
        max_value = float(np.max(finite_scale)) if finite_scale.size else 0.0
        finite_A = np.where(np.isfinite(A), A, 0.0)
        if max_value <= 0.0:
            return np.zeros_like(finite_A, dtype=np.float64)
        return finite_A / max_value

    @staticmethod
    def _interp_image_to_f(f_src: np.ndarray, A_src: np.ndarray, f_ref: np.ndarray) -> np.ndarray:
        f_src = np.asarray(f_src, dtype=np.float64)
        A_src = np.asarray(A_src, dtype=np.float64)
        f_ref = np.asarray(f_ref, dtype=np.float64)
        if f_src.shape == f_ref.shape and np.allclose(f_src, f_ref, rtol=0.0, atol=1e-9):
            return A_src.copy()
        out = np.full((f_ref.size, A_src.shape[1]), np.nan, dtype=np.float64)
        for j in range(A_src.shape[1]):
            col = A_src[:, j]
            valid = np.isfinite(col)
            if np.count_nonzero(valid) >= 2:
                out[:, j] = np.interp(f_ref, f_src[valid], col[valid], left=np.nan, right=np.nan)
            elif np.count_nonzero(valid) == 1:
                idx = int(np.flatnonzero(valid)[0])
                out[np.argmin(np.abs(f_ref - f_src[idx])), j] = col[idx]
        return out

    def _display_dispersion_image(
        self,
        f: np.ndarray,
        c: np.ndarray,
        A: np.ndarray,
        status: str,
        preserve_view: bool = False,
        view_range: tuple[float, float, float, float] | None = None,
    ) -> None:
        saved_view = view_range
        saved_raw_view = None if view_range is not None else (
            self._current_raw_dispersion_view_range() if preserve_view else None
        )
        dc = float(c[1] - c[0]) if len(c) > 1 else 1.0
        # Zona de aliasing espacial (c < 2*dx*f) y techo de apertura (c > L*f):
        # ya NO se recorta la imagen ahi -- se sigue graficando el plano
        # entero, y esos limites quedan solo como curvas de sugerencia
        # dibujadas encima (_draw_alias_boundary / _draw_lambda_max_boundary).
        # Se arma igual una version enmascarada nada mas para que la
        # normalizacion de color (por fila o global, toggle "Intensidad por
        # frecuencia") ajuste el contraste a la banda valida de picking, sin
        # que quede lavado por un maximo que caiga fuera de esa banda; lo que
        # se termina mostrando es siempre el plano completo, sin recortar.
        A_display = np.asarray(A, dtype=np.float64).copy()
        A_scale = A_display.copy()
        dx = self._geophone_spacing_m
        length = self._array_length_m
        if dx and dx > 0:
            alias_c = 2.0 * dx * f[:, None]
            A_scale[c[None, :] < alias_c] = np.nan
        if length and length > 0:
            lam_c = length * f[:, None]
            A_scale[c[None, :] > lam_c] = np.nan
        A_display = self._combined_display_array(A_display, A_scale)
        A_display = self._display_intensity_image(A_display)
        x_axis, A_display = self._axis_image_data(f, A_display)
        dx_axis = float(x_axis[1] - x_axis[0]) if len(x_axis) > 1 else 1.0
        level_max = self._dispersion_level_max()
        # Al redibujar por un toggle de vista (preserve_view=True) el usuario
        # puede haber ajustado a mano el rango de colores/histograma; se
        # guarda antes de setImage() (que si no, lo pisa) y se reaplica
        # despues en vez de forzar siempre (0, level_max).
        histogram = self.image_view.ui.histogram
        saved_levels = None
        saved_hist_range = None
        if preserve_view:
            try:
                saved_levels = histogram.getLevels()
            except Exception:
                saved_levels = None
            try:
                saved_hist_range = histogram.getHistogramRange()
            except Exception:
                saved_hist_range = None
        self.image_view.setImage(
            A_display,
            pos=[float(x_axis[0]), float(c[0])],
            scale=[dx_axis, dc],
            autoRange=saved_view is None and saved_raw_view is None,
            autoLevels=False,
            levels=saved_levels if saved_levels is not None else (0.0, level_max),
            autoHistogramRange=False,
        )
        try:
            self._plot_item.vb.setAspectLocked(False)
        except Exception:
            pass
        try:
            levels_min, levels_max = saved_levels if saved_levels is not None else (0.0, level_max)
            self.image_view.setLevels(levels_min, levels_max)
            if saved_hist_range is not None:
                histogram.setHistogramRange(saved_hist_range[0], saved_hist_range[1], padding=0.0)
            else:
                histogram.setHistogramRange(0.0, level_max, padding=0.0)
        except Exception:
            pass
        # Eje log real (AxisItem.logMode): la posicion de la imagen ya esta en
        # log10(f) (ver _axis_image_data); esto solo cambia el dibujo de los
        # ticks para que la separacion sea logaritmica pero las etiquetas
        # sigan mostrando Hz (10, 20, ..., 100, ...) en vez de log10(f).
        self._plot_item.getAxis("bottom").setLogMode(self._freq_log_scale)
        self._plot_item.setLabel("bottom", "Frecuencia", units="Hz")
        self._plot_item.setLabel("left", "Velocidad de fase [m/s]")
        try:
            self.image_view.setColorMap(self._dispersion_colormap())
        except Exception:
            pass
        self._draw_alias_boundary(f, c)
        self._draw_lambda_max_boundary(f, c)
        self._refresh_pick_scatter()
        self._refresh_m0_draw()
        if saved_view is not None:
            self._restore_dispersion_view_range(saved_view)
        elif saved_raw_view is not None:
            self._restore_raw_dispersion_view_range(saved_raw_view)
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
        self.info_label.setText(status)

    def _recombine_group_results(self, preserve_view: bool = False) -> None:
        if not self._group_results:
            return
        first = self._group_results[sorted(self._group_results)[0]]
        f = np.asarray(first["f"], dtype=np.float64)
        c = np.asarray(first["c"], dtype=np.float64)
        combined = np.zeros((f.size, c.size), dtype=np.float64)
        parts: list[str] = []
        for gid in sorted(self._group_results):
            result = self._group_results[gid]
            weight = float(self._group_weights.get(gid, 1.0))
            combined += weight * np.asarray(result["A_norm"], dtype=np.float64)
            parts.append(f"G{gid} w={weight:.2f}")
        # combined queda CRUDO (sin normalizar): la normalizacion de color se
        # aplica en _display_dispersion_image, despues de enmascarar la zona
        # de aliasing/lambda_max, para que el maximo por fila se calcule solo
        # sobre la banda valida de picking. self._last_result mantiene la
        # grilla entera sin enmascarar para que el pick (argmax) tenga todos
        # los datos.
        self._last_result = (f, c, combined)
        self._display_dispersion_image(
            f,
            c,
            combined,
            f"Imagen combinada lista: {', '.join(parts)}. "
            f"f<={f[-1]:.1f} Hz, c=[{c[0]:.0f},{c[-1]:.0f}] m/s. "
            "Ahora marca la cresta: 'Auto-pick' o click manual.",
            preserve_view=preserve_view,
        )

    def _legacy_single_group_if_needed(self) -> None:
        if self._raw_groups or self._raw_matrix is None or self._raw_time is None or self._raw_distances is None:
            return
        distances = [float(d) for d in self._raw_distances]
        self._raw_groups = {
            1: {
                "name": "Grupo 1",
                "time": np.asarray(self._raw_time, dtype=np.float64),
                "distances": distances,
                "matrix": np.asarray(self._raw_matrix, dtype=np.float64),
                "spacing": self._estimate_spacing_m(distances),
                "length": self._estimate_array_length(distances),
            }
        }
        self._group_weights.setdefault(1, 1.0)
        self._update_combined_geometry()
        self._update_weight_controls()

    def _group_summary(self) -> str:
        return ", ".join(
            f"G{gid}({len(self._raw_groups[gid]['distances'])} canales)"
            for gid in sorted(self._raw_groups)
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

    def run_auto(
        self,
        common_time: np.ndarray,
        distances: list[float],
        matrix: np.ndarray,
        group_id: int = 1,
        group_name: str | None = None,
    ) -> None:
        """Flujo MASW completo sin interaccion: imagen con los parametros
        actuales de la pestaña (defaults pensados para el tendido de campo),
        auto-pick con filtros de calidad (umbral de amplitud adaptativo,
        limites fisicos del tendido, rechazo de outliers) e inversion Monte
        Carlo. Cada parametro elegido queda visible en los controles para
        poder revisarlo o repetir el proceso a mano."""
        self.set_group_data(common_time, distances, matrix, group_id=group_id, group_name=group_name)
        if not self._raw_groups:
            return
        self._calculate()
        if self._last_result is None:
            return
        f, c, A = self._last_result
        fell_back = False
        try:
            # auto_extract_dispersion_curve espera amplitud normalizada por
            # frecuencia (su umbral de calidad compara contra el pico de cada
            # fila); A puede venir globalmente normalizada si el toggle
            # "Intensidad por frecuencia" esta apagado, asi que se normaliza
            # por fila aca sin importar el estado del toggle de visualizacion.
            freqs, c_obs = auto_extract_dispersion_curve(
                f, c, self._normalize_dispersion_image(A), np.asarray(self._raw_distances, dtype=np.float64)
            )
            self._reset_all_modes()
            self.picks.update({float(fv): float(cv) for fv, cv in zip(freqs, c_obs)})
            self.pick_fmin_spin.setValue(float(freqs[0]))
            self.pick_fmax_spin.setValue(float(freqs[-1]))
        except ValueError:
            # El extractor "coherente" es estricto y en capturas ruidosas puede
            # no encontrar cresta. En vez de abortar, cae al auto-pick simple
            # (cresta por frecuencia dentro de la banda valida 2*dx <= lambda <= L).
            fell_back = True
            self._reset_all_modes()
            self._auto_pick()
            if not self.picks:
                QMessageBox.warning(
                    self, "Auto inversion",
                    "No se pudo extraer una curva automatica (ni con el pick simple). "
                    "Proba a mano: defini regiones por modo y usa 'Auto-pick'.",
                )
                return
            ks = sorted(self.picks)
            freqs = np.array(ks, dtype=np.float64)
        self._refresh_mode_combo()
        self._refresh_pick_scatter()
        modo_txt = " (pick simple: cresta por frecuencia)" if fell_back else " (coherente + rechazo de outliers)"
        self.info_label.setText(
            f"Auto-pick: {len(self.picks)} puntos en {freqs[0]:.1f}-{freqs[-1]:.1f} Hz{modo_txt}."
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
        self._legacy_single_group_if_needed()
        if not self._raw_groups:
            QMessageBox.warning(self, "MASW", "Todavia no hay datos. Anda a la pestaña Waterfall y presiona 'Ver MASW'.")
            return
        if self.cmax_spin.value() <= self.cmin_spin.value():
            QMessageBox.warning(self, "MASW", "c max debe ser mayor que c min.")
            return
        if self.fmax_spin.value() <= self.fmin_spin.value():
            QMessageBox.warning(self, "MASW", "f max debe ser mayor que f min.")
            return
        self.calc_btn.setEnabled(False)
        self.info_label.setText("Calculando imagen de dispersion por grupo (metodo phase-shift)...")
        QApplication.processEvents()
        try:
            f_ref: np.ndarray | None = None
            c_ref: np.ndarray | None = None
            results: dict[int, dict[str, object]] = {}
            for group_id in sorted(self._raw_groups):
                group = self._raw_groups[group_id]
                time = np.asarray(group["time"], dtype=np.float64)
                matrix = np.asarray(group["matrix"], dtype=np.float64)
                distances = np.asarray(group["distances"], dtype=np.float64)
                u = matrix.T  # (n_time, n_channels)
                fs = 1.0 / float(np.median(np.diff(time)))
                f, c, A = phase_shift_dispersion_image(
                    u,
                    distances,
                    fs,
                    c_min=float(self.cmin_spin.value()),
                    c_max=float(self.cmax_spin.value()),
                    c_step=float(self.cstep_spin.value()),
                    f_max=float(self.fmax_spin.value()),
                    f_min=float(self.fmin_spin.value()),
                )
                if f_ref is None:
                    f_ref = f
                    c_ref = c
                    A_on_ref = A
                else:
                    if c_ref is None or c.shape != c_ref.shape or not np.allclose(c, c_ref):
                        raise ValueError("Los grupos generaron grillas de velocidad distintas")
                    A_on_ref = self._interp_image_to_f(f, A, f_ref)
                results[group_id] = {
                    "f": np.asarray(f_ref, dtype=np.float64),
                    "c": np.asarray(c_ref, dtype=np.float64),
                    "A": np.asarray(A_on_ref, dtype=np.float64),
                    "A_norm": self._normalize_dispersion_image(A_on_ref),
                    "name": str(group.get("name", _group_name(group_id))),
                }
        except Exception as exc:
            QMessageBox.critical(self, "MASW", f"No se pudo calcular: {exc}")
            self.calc_btn.setEnabled(True)
            return
        self._group_results = results
        self._recombine_group_results()
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
                        "pos": (self._freq_to_x(float(fv)), float(picks[fv])),
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
            self._freq_to_x_array(f[inside]),
            np.clip(c_alias[inside], float(c[0]), float(c[-1])),
            pen=pen,
            connect="finite",
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
            self._freq_to_x_array(f[inside]),
            np.clip(c_lam[inside], float(c[0]), float(c[-1])),
            pen=pen,
            connect="finite",
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
                display_xs = [self._freq_to_x(float(x)) for x in xs]
                _append_break()
                line_x.extend(display_xs + [display_xs[0]])
                line_y.extend(ys + [ys[0]])
                for x, y in poly:
                    vert_spots.append(
                        {"pos": (self._freq_to_x(float(x)), float(y)), "brush": pg.mkBrush(color),
                         "pen": pg.mkPen("#000000", width=1), "size": 8}
                    )
                lbl = pg.TextItem(f"M{mode}", color=color, anchor=(0.5, 1.0))
                lbl.setPos(float(np.nanmean(display_xs)), float(np.max(ys)))
                lbl.setZValue(47)
                self._plot_item.addItem(lbl)
                self._region_labels.append(lbl)

        if self._m0_drawing and self._m0_draft:
            color = self._mode_color(self._active_mode)
            dxs = [p[0] for p in self._m0_draft]
            dys = [p[1] for p in self._m0_draft]
            display_dxs = [self._freq_to_x(float(x)) for x in dxs]
            _append_break()
            line_x.extend(display_dxs)
            line_y.extend(dys)
            for x, y in self._m0_draft:
                vert_spots.append(
                    {"pos": (self._freq_to_x(float(x)), float(y)), "brush": pg.mkBrush(color),
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
        f_val, c_val = self._x_to_freq(float(point.x())), float(point.y())
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
                f"Región M{self._active_mode}: {len(self._m0_draft)} vertice(s). Segui clickeando y "
                "presiona 'Cerrar región' cuando tengas al menos 3."
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
                f_val = self._x_to_freq(float(start_point.x()))
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
        f_val, c_val = self._x_to_freq(float(point.x())), float(point.y())
        amp_txt = ""
        if self._last_result is not None:
            f, c, A = self._last_result
            if f.size and c.size and f[0] <= f_val <= f[-1] and c[0] <= c_val <= c[-1]:
                dc = float(c[1] - c[0]) if c.size > 1 else 1.0
                i = int(np.argmin(np.abs(f - f_val)))
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

    def _modes_with_curve(self, min_points: int = 3) -> list[int]:
        """Modos con al menos `min_points` picks (curvas invertibles)."""
        return [m for m in sorted(self.picks_by_mode) if len(self.picks_by_mode[m]) >= min_points]

    def _curves_by_mode_for_inversion(self, min_points: int = 3) -> dict[int, tuple[np.ndarray, np.ndarray]]:
        out: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        for m, picks in self.picks_by_mode.items():
            if len(picks) < min_points:
                continue
            fs = np.array(sorted(picks), dtype=np.float64)
            cs = np.array([picks[k] for k in sorted(picks)], dtype=np.float64)
            out[int(m)] = (fs, cs)
        return out

    def _backend_changed(self, _idx: int = 0) -> None:
        if not hasattr(self, "backend_combo"):
            return
        key = self.backend_combo.currentData()
        if key is None:
            return
        kind = masw_backends.backend_kind(key)
        status = masw_backends.backend_status(key)
        n = len(self._modes_with_curve())
        if kind == "export":
            self.run_inv_btn.setText("Exportar / lanzar")
            hint = "Exporta las curvas y, si está en el PATH, lanza la herramienta."
        else:
            self.run_inv_btn.setText("Correr inversión")
            scope = "todas las curvas" if key in ("evodcinv", "disba_mc", "adsurf") else "1 modo (fundamental/activo)"
            hint = f"Usa {scope}."
        self.backend_status_label.setText(f"{status} · {hint} · {n} modo(s) con curva")

    def _run_selected_inversion(self) -> None:
        if self._inverting:
            return
        key = self.backend_combo.currentData() if hasattr(self, "backend_combo") else "maswavespy"
        if masw_backends.backend_kind(key) == "export":
            self._export_to_tool(key, launch=True)
            return
        if not masw_backends.backend_available(key):
            QMessageBox.warning(self, "MASW", f"El motor no está disponible: {masw_backends.backend_status(key)}")
            return
        if key == "maswavespy":
            # Un solo modo: flujo Monte Carlo en vivo sobre el modo activo/fundamental.
            modes = self._modes_with_curve()
            if modes and self._active_mode not in modes:
                self._active_mode = modes[0]
                self.picks = self.picks_by_mode[self._active_mode]
                self._refresh_mode_combo()
            self._run_inversion()
            return
        # evodcinv / disba_mc: inversion conjunta con TODAS las curvas.
        self._run_backend_multimodal(key)

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

    def _export_to_tool(self, tool: str, launch: bool = False) -> None:
        """Exporta las curvas de todos los modos para una herramienta externa
        (o formato generico) y, si `launch`, intenta abrirla."""
        curves_by_mode = self._curves_by_mode_for_inversion()
        if not curves_by_mode:
            QMessageBox.warning(self, "MASW", "No hay curvas pickeadas (algun modo con 3+ picks).")
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Carpeta para exportar las curvas")
        if not out_dir:
            return
        try:
            paths = masw_backends.export_curves(tool, curves_by_mode, out_dir)
        except Exception as exc:
            QMessageBox.critical(self, "MASW", f"No pude exportar: {exc}")
            return
        msg = "Exportado:\n" + "\n".join(str(p) for p in paths)
        if launch and masw_backends.backend_kind(tool) == "export":
            launched, note = masw_backends.launch_tool(tool, paths)
            msg += "\n\n" + note
        QMessageBox.information(self, "MASW", msg)
        self.inv_status_label.setText(f"Curvas exportadas ({len(curves_by_mode)} modos) → {out_dir}")

    def _run_backend_multimodal(self, key: str) -> None:
        if self._inverting:
            return
        curves_by_mode = self._curves_by_mode_for_inversion()
        if not curves_by_mode:
            QMessageBox.warning(
                self, "MASW",
                "Se necesita al menos un modo con 3+ picks. Defini regiones por modo y corre 'Auto-pick'.",
            )
            return
        if not masw_backends.backend_available(key):
            QMessageBox.warning(self, "MASW", f"Motor no disponible: {masw_backends.backend_status(key)}")
            return
        self._inverting = True
        self.run_inv_btn.setEnabled(False)
        self.inv_status_label.setText(
            f"Corriendo inversión ({masw_backends.backend_label(key)}) con {len(curves_by_mode)} modo(s)... puede tardar."
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            res = masw_backends.run_inversion(
                key,
                curves_by_mode,
                n_layers=int(self.nlayers_spin.value()),
                maxiter=int(max(self.niter_spin.value() // 10, 30)),
                popsize=20,
                n_iter=int(self.niter_spin.value()),
                nu=float(self.nu_spin.value()),
                rho=float(self.rho_spin.value()),
                bs=float(self.bs_spin.value()),
                bh=float(self.bh_spin.value()),
            )
        except Exception as exc:
            QMessageBox.critical(self, "MASW", f"La inversión falló: {exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
            self._inverting = False
            self.run_inv_btn.setEnabled(True)
        self._mm_result = res
        self._display_multimodal_result(res)

    # Alias historico (por si algo externo lo llama): usa evodcinv.
    def _run_multimodal_inversion(self) -> None:
        self._run_backend_multimodal("evodcinv")

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
        engine = res.get("engine", "multimodo")
        lines = [
            f"INVERSION ({engine})",
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
            f"Inversión lista ({engine}): {len(res['modes'])} modo(s), desajuste {res['misfit']:.4f} km/s. "
            "El perfil está en '3. Perfil Vs'."
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
            "regions_by_mode": {
                str(mode): [[[float(x), float(y)] for (x, y) in poly] for poly in polys]
                for mode, polys in self._regions_by_mode.items()
            },
            "raw_groups": {
                str(group_id): {
                    "name": str(group.get("name", _group_name(group_id))),
                    "spacing": (
                        float(group["spacing"]) if group.get("spacing") is not None else None
                    ),
                    "length": (
                        float(group["length"]) if group.get("length") is not None else None
                    ),
                }
                for group_id, group in self._raw_groups.items()
            },
            "group_weights": {
                str(group_id): float(weight)
                for group_id, weight in sorted(self._group_weights.items())
            },
            "weight_slider_max": float(self._weight_slider_max),
            "display_options": {
                "freq_log": bool(self._freq_log_scale),
                "intensity_log": bool(self._intensity_log_scale),
                "intensity_per_freq": bool(self._intensity_per_freq),
            },
            "active_data_group": int(self._active_data_group_id),
            "geophone_spacing_m": (
                float(self._geophone_spacing_m) if self._geophone_spacing_m else None
            ),
            "array_length_m": (
                float(self._array_length_m) if self._array_length_m else None
            ),
            "inner_tab": int(self.inner_tabs.currentIndex()),
            "backend": self.backend_combo.currentData() if hasattr(self, "backend_combo") else None,
            "has_data": bool(self._raw_groups) or self._raw_matrix is not None,
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
        if self._raw_groups:
            group_ids = np.array(sorted(self._raw_groups), dtype=np.int32)
            arrays["masw_group_ids"] = group_ids
            for group_id in group_ids:
                group = self._raw_groups[int(group_id)]
                prefix = f"masw_g{int(group_id)}"
                arrays[f"{prefix}_time"] = np.asarray(group["time"], dtype=np.float64)
                arrays[f"{prefix}_distances"] = np.asarray(group["distances"], dtype=np.float64)
                arrays[f"{prefix}_matrix"] = np.asarray(group["matrix"], dtype=np.float64)
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

        self._group_weights = {
            int(group_id): float(weight)
            for group_id, weight in state.get("group_weights", {}).items()
        } if isinstance(state.get("group_weights"), dict) else {}
        if "weight_slider_max" in state:
            self._weight_slider_max = max(0.01, float(state["weight_slider_max"]))
            if hasattr(self, "weight_max_spin"):
                self.weight_max_spin.blockSignals(True)
                self.weight_max_spin.setValue(self._weight_slider_max)
                self.weight_max_spin.blockSignals(False)
        display_options = state.get("display_options", {}) if isinstance(state.get("display_options"), dict) else {}
        self._freq_log_scale = bool(display_options.get("freq_log", False))
        self._intensity_log_scale = bool(display_options.get("intensity_log", False))
        self._intensity_per_freq = bool(display_options.get("intensity_per_freq", False))
        for btn, checked in (
            (getattr(self, "freq_log_btn", None), self._freq_log_scale),
            (getattr(self, "intensity_per_freq_btn", None), self._intensity_per_freq),
            (getattr(self, "intensity_log_btn", None), self._intensity_log_scale),
        ):
            if btn is not None:
                btn.blockSignals(True)
                btn.setChecked(bool(checked))
                btn.blockSignals(False)
        raw_group_meta = state.get("raw_groups", {}) if isinstance(state.get("raw_groups"), dict) else {}
        if "masw_group_ids" in arrays:
            self._raw_groups = {}
            for raw_gid in np.asarray(arrays["masw_group_ids"]).astype(int):
                gid = int(raw_gid)
                prefix = f"masw_g{gid}"
                if f"{prefix}_matrix" not in arrays:
                    continue
                distances = [float(d) for d in arrays.get(f"{prefix}_distances", np.array([], dtype=np.float64))]
                meta = raw_group_meta.get(str(gid), {}) if isinstance(raw_group_meta, dict) else {}
                self._raw_groups[gid] = {
                    "name": str(meta.get("name", _group_name(gid))),
                    "time": np.asarray(arrays[f"{prefix}_time"], dtype=np.float64),
                    "distances": distances,
                    "matrix": np.asarray(arrays[f"{prefix}_matrix"], dtype=np.float64),
                    "spacing": (
                        float(meta["spacing"]) if meta.get("spacing") is not None
                        else self._estimate_spacing_m(distances)
                    ),
                    "length": (
                        float(meta["length"]) if meta.get("length") is not None
                        else self._estimate_array_length(distances)
                    ),
                }
                self._group_weights.setdefault(gid, 1.0)
            self._active_data_group_id = int(state.get("active_data_group", 1) or 1)
            self._update_combined_geometry()
            self._update_weight_controls()
        elif "masw_matrix" in arrays:
            self._raw_time = np.asarray(arrays["masw_time"], dtype=np.float64)
            self._raw_distances = [float(d) for d in arrays["masw_distances"]]
            self._raw_matrix = np.asarray(arrays["masw_matrix"], dtype=np.float64)
            self._geophone_spacing_m = (
                state.get("geophone_spacing_m") or self._estimate_spacing_m(self._raw_distances)
            )
            self._array_length_m = (
                state.get("array_length_m") or self._estimate_array_length(self._raw_distances)
            )
            self._legacy_single_group_if_needed()

        rbm: dict[int, list[list[tuple[float, float]]]] = {}
        raw_rbm = state.get("regions_by_mode")
        if isinstance(raw_rbm, dict):
            for mode_str, polys in raw_rbm.items():
                try:
                    mode = int(mode_str)
                except (TypeError, ValueError):
                    continue
                rbm[mode] = [[(float(x), float(y)) for (x, y) in poly] for poly in polys]
        else:
            # Compat con el formato viejo `regions` (lista plana, indice = modo).
            for mode, poly in enumerate(state.get("regions", [])):
                rbm[mode] = [[(float(x), float(y)) for (x, y) in poly]]
        if not rbm:
            rbm = {0: []}
        self._regions_by_mode = rbm
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

        backend = state.get("backend")
        if backend and hasattr(self, "backend_combo"):
            idx = self.backend_combo.findData(backend)
            if idx >= 0:
                self.backend_combo.setCurrentIndex(idx)

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
        self._refresh_mode_legend()
        self._refresh_pick_scatter()
        self._refresh_m0_draw()
        self._refresh_inv_observed()
        self._backend_changed()
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


