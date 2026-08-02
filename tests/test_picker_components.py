def test_extracted_picker_components_are_importable():
    from ui.color_preview_box import ColorPreviewBox
    from ui.picker_panes import LabPane, PaneWithModeButton, WheelPane

    assert ColorPreviewBox.__name__ == "ColorPreviewBox"
    assert PaneWithModeButton.__name__ == "PaneWithModeButton"
    assert WheelPane.__name__ == "WheelPane"
    assert LabPane.__name__ == "LabPane"
