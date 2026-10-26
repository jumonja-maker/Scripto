# Third-party components

The MIT license at the repository root covers ScriptoLib's own code. It does
not replace the licenses of bundled dependencies. No proprietary media,
downloaded movies or music, website accounts, player engines or browser engine
are included in milestone 1.

| Component | Bundled version | License / source |
|---|---|---|
| CPython Windows embedded runtime | 3.12.10 | Python Software Foundation License; `runtime/LICENSE.txt`; [source release](https://www.python.org/downloads/release/python-31210/) |
| PySide6 Essentials, Shiboken6, Qt libraries | 6.10.3 | Qt for Python open-source LGPL/GPL terms and component-specific notices; [Qt for Python source](https://code.qt.io/pyside/pyside-setup.git/tree/?h=v6.10.3), [Qt source repositories](https://code.qt.io/cgit/qt/) |
| Manrope font | bundled variable font | SIL Open Font License 1.1; `assets/Manrope-OFL.txt`; [upstream](https://github.com/google/fonts/tree/main/ofl/manrope) |
| NSIS installer engine | build-tool version recorded by builder | [NSIS license](https://nsis.sourceforge.io/License) |

Qt/PySide remain dynamically loaded, replaceable libraries in
`runtime/Lib/site-packages`. ScriptoLib does not prohibit replacing those
libraries or reverse engineering for debugging such modifications. License
texts and available upstream notices are bundled with the distribution. Build
inputs and SHA256 values are recorded in `app/build-inputs.json`; application
source and build scripts are available at https://github.com/jumonja-maker/Scripto.

The approved Figma screen supplies the original navigation SVG assets. Local
media artwork remains user data; demonstration movie posters are not inserted
into the user's catalogue.
