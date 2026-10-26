# OMaaX document runtime third-party components

This runtime is separate from the DeepSeek Harness agent runtime. It does not
upgrade or embed a newer agent base. OMaaX model credentials are not distributed
with these components.

## LibreOffice Kit 0.1.5

- Source: https://github.com/deepseek-ai/dsh-libreoffice-kit
- Node package: https://www.npmjs.com/package/@deepseek-ai/libreoffice-kit/v/0.1.5
- License: MPL-2.0. Included DeepSeek-derived integration portions also retain
  their upstream MIT notices. LibreOffice and its third-party components carry
  their own licenses.
- The complete, unmodified platform package must be shipped, including
  `LICENSE`, `NOTICE`, `prebuilds.json`, `sources/`, and `licenses/` where supplied.
  These files are kept under `node_modules/@deepseek-ai/` by stage-documents.mjs.
- Corresponding source recipes, upstream revisions, patches, build metadata and
  redistribution notices are in each platform package's `sources/` and
  `licenses/` directories. No kit or engine code is encrypted or obfuscated.
- Conversion runs locally. It does not call Microsoft Office, WPS, or a user's
  LibreOffice installation. Windows builds include the app-local Microsoft
  Visual C++ runtime described below; users need no separate runtime installer.
- System fonts are used; no Windows/Office fonts are copied or redistributed.
  Layout may differ from Microsoft Office when requested fonts are unavailable.

## Microsoft Visual C++ runtime (Windows x64)

- Copyright Microsoft Corporation. These unmodified release DLLs are Microsoft
  redistributable components, not part of the OMaaX MIT license or LibreOffice.
- Source: Visual Studio 2022 `VC/Redist/MSVC/14.44.35112/x64/Microsoft.VC143.CRT`.
  File version: `14.44.35211.0`. No files are copied from Windows system folders.
- The entire ten-file CRT set is deployed beside `libreoffice-kit.exe`; its
  contents and SHA-256 hashes are pinned in `windows-crt.lock.json`.
- Microsoft redistribution terms and file list:
  https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution
- Microsoft deployment documentation:
  https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files?view=msvc-170
- Redistribution is subject to the applicable Microsoft Visual Studio license
  terms. The official Redist directory contains no separate LICENSE file; these
  links and the pinned manifest record its provenance without inventing a license.
- Building a fresh Windows resource tree requires the matching official Redist
  files. An already staged or installed application verifies and uses its bundled
  copies and does not require Visual Studio or administrator access.

## Python document packages

The dedicated environment installs the pinned requirements from PyPI. Each wheel
retains its original license and metadata in its `.dist-info` directory.

| Package | License | Source |
| --- | --- | --- |
| python-docx | MIT | https://github.com/python-openxml/python-docx |
| python-pptx | MIT | https://github.com/scanny/python-pptx |
| Pillow | HPND | https://github.com/python-pillow/Pillow |
| pypdf | BSD-3-Clause | https://github.com/py-pdf/pypdf |
| lxml | BSD-3-Clause and bundled libxml2/libxslt notices | https://github.com/lxml/lxml |
| typing_extensions | PSF-2.0 | https://github.com/python/typing_extensions |
| XlsxWriter | BSD-2-Clause | https://github.com/jmcnamara/XlsxWriter |

PyMuPDF is not a dependency of this runtime. Office previews use the bundled
LibreOffice Kit directly; PDF inspection uses pypdf.
