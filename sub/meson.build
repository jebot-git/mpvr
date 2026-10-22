osd_font = custom_target('osd_font.bdf',
    input: 'osd_font.bdf',
    output: 'osd_font.bdf.inc',
    command: [file2string, '@INPUT@', '@OUTPUT@', '@SOURCE_ROOT@'],
)
sources += osd_font
