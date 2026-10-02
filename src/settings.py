from .logging_utils import get_logger

logger = get_logger(__name__)


class THWToolboxSettings:
    # Default values for settings
    NEW_ICON_SCALING_WITH_MAP_DEFAULT = False
    NEW_ICON_FIXED_SIZE_DEFAULT = False
    NEW_ICON_SIZE_DEFAULT = 50
    NEW_ICON_CRS_DEFAULT = "MGRS"
    LABEL_ENABLE_DEFAULT = True
    LABEL_FONT_SIZE_DEFAULT_UM = 6000
    LABEL_BUFFER_SIZE_DEFAULT_UM = 1000

    # Save-File values
    PLUGIN_NAME = "taktischezeichen"

    def __init__(self):
        self._new_icon_scaling_with_map = self.NEW_ICON_SCALING_WITH_MAP_DEFAULT
        self._new_icon_fixed_size = self.NEW_ICON_FIXED_SIZE_DEFAULT
        self._new_icon_size = self.NEW_ICON_SIZE_DEFAULT
        self._new_icon_crs = self.NEW_ICON_CRS_DEFAULT

        self._label_enable = self.LABEL_ENABLE_DEFAULT
        self._label_font_size_mm = self.LABEL_FONT_SIZE_DEFAULT_UM / 1000.0
        self._label_buffer_size_mm = self.LABEL_BUFFER_SIZE_DEFAULT_UM / 1000.0

    @property
    def new_icon_scaling_with_map(self) -> bool:
        """Initial `scale_with_map` value for new symbols.

        Despite the name, True means a fixed screen size (millimetres) — same
        inverted meaning as the feature attribute, see layer.renderer.size_unit.
        Kept as-is so settings saved in existing projects stay valid.
        """
        return self._new_icon_scaling_with_map

    @new_icon_scaling_with_map.setter
    def new_icon_scaling_with_map(self, value):
        if not isinstance(value, bool):
            raise ValueError("Scaling must be a bool")
        self._new_icon_scaling_with_map = value

    @property
    def new_icon_fixed_size(self) -> bool:
        return self._new_icon_fixed_size

    @new_icon_fixed_size.setter
    def new_icon_fixed_size(self, value):
        if not isinstance(value, bool):
            raise ValueError("Fixed size must be a bool")
        self._new_icon_fixed_size = value

    @property
    def new_icon_size(self) -> int:
        return self._new_icon_size

    @new_icon_size.setter
    def new_icon_size(self, value):
        if not isinstance(value, int) or value <= 0:
            raise ValueError("Icon size must be a positive integer")
        self._new_icon_size = value

    @property
    def new_icon_crs(self) -> str:
        return self._new_icon_crs

    @new_icon_crs.setter
    def new_icon_crs(self, value):
        if not isinstance(value, str):
            raise ValueError("CRS must be a string")
        self._new_icon_crs = value

    @property
    def label_enable(self) -> bool:
        return self._label_enable

    @label_enable.setter
    def label_enable(self, value):
        if not isinstance(value, bool):
            raise ValueError("Label Enable must be a bool")
        self._label_enable = value

    @property
    def label_font_size_mm(self) -> int:
        return self._label_font_size_mm

    @label_font_size_mm.setter
    def label_font_size_mm(self, value):
        if not isinstance(value, (int, float)) or value <= 0:
            raise ValueError("Font size must be a positive number")
        self._label_font_size_mm = int(value)

    @property
    def label_buffer_size_mm(self) -> int:
        return self._label_buffer_size_mm

    @label_buffer_size_mm.setter
    def label_buffer_size_mm(self, value):
        if not isinstance(value, (int, float)) or value <= 0:
            raise ValueError("Buffer size must be a positive number")
        self._label_buffer_size_mm = int(value)

    def load_settings(self, proj):
        """Loads settings automatically from the defined private parameters"""
        for attr_name in dir(self):
            if attr_name.startswith("_") and not attr_name.startswith("__"):
                private_field = attr_name
                config_key = attr_name[1:]  # '_new_icon_scaling_with_map' → 'new_icon_scaling_with_map'

                # logic for config key transformations
                if private_field.endswith("_mm"):
                    config_key = config_key.replace("_mm", "_um")
                    default_value = getattr(self, f"{private_field[1:-3]}_DEFAULT_UM".upper())
                    loaded_value, ok = proj.readNumEntry(self.PLUGIN_NAME, config_key, default_value)
                    final_value = (loaded_value if ok else default_value) / 1000.0
                else:
                    default_value = getattr(self, f"{private_field[1:]}_DEFAULT".upper())
                    if isinstance(default_value, bool):
                        loaded_value, ok = proj.readBoolEntry(self.PLUGIN_NAME, config_key, default_value)
                    elif isinstance(default_value, int):
                        loaded_value, ok = proj.readNumEntry(self.PLUGIN_NAME, config_key, default_value)
                    else:
                        loaded_value, ok = proj.readEntry(self.PLUGIN_NAME, config_key, str(default_value))
                    final_value = loaded_value if ok else default_value

                # Set via public property (uses your existing setters with validation)
                public_prop = private_field[1:]  # '_new_icon_scaling_with_map' → 'new_icon_scaling_with_map'
                if hasattr(self, public_prop):
                    setattr(self, public_prop, final_value)

                logger.debug("Tried to load %s with result %s and value %s", config_key, ok, final_value)

        proj.setDirty(True)

    def save_settings(self, proj):
        """Saves all private parameters into the project"""

        for attr_name in dir(self):
            if attr_name.startswith("_") and not attr_name.startswith("__"):
                value = getattr(self, attr_name)
                config_key = attr_name[1:]  # Do not use the starting _ for key name

                if isinstance(value, bool):
                    proj.writeEntryBool(self.PLUGIN_NAME, config_key, value)
                elif isinstance(value, str):
                    proj.writeEntry(self.PLUGIN_NAME, config_key, value)
                elif attr_name.endswith("_mm"):
                    config_key = config_key.replace("_mm", "_um")
                    proj.writeEntry(self.PLUGIN_NAME, config_key, int(value * 1000))
                else:
                    proj.writeEntry(self.PLUGIN_NAME, config_key, int(value))

        proj.setDirty(True)
