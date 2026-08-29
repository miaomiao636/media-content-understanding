from scripts.mcu import slug


def test_slug_removes_unsafe_filename_characters():
    assert slug('A/B:C*D?E"F<G>H|I') == "A_B_C_D_E_F_G_H_I"


def test_slug_has_fallback():
    assert slug(" ... ") == "未命名视频"
