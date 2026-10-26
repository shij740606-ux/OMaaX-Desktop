---
name: omaax-documents
description: Create and edit Word DOCX reports, PowerPoint PPTX slides, and PDF documents from actual project results. Use for 把结果整理成Word/PPT/PDF、报告、汇报、幻灯片、文档、导出PDF、继续修改文稿或第三页、插入图片表格. Save editable sources and revisions to the current project; no Microsoft Office, WPS or separate model key required.
---

# Project documents

Use this skill when the user asks to turn results into Word, PPT or PDF, or to
continue editing one of those documents. Complete generation with the supplied
local runner. Merely describing an outline is not delivery unless the user asks
for an outline. Use the existing conversation model; do not call another model
endpoint or request a key.

## Inputs and project files

1. Read the relevant conversation, project result tables and actual figures.
   Inspect tabular values and figure dimensions before writing about them. Keep
   measured results separate from interpretations and limitations. Never invent
   data, citations, completed analyses or a file that does not exist.
2. Work inside the current local project directory. Use a meaningful subfolder,
   for example `documents/rice_analysis/`, containing the deliverable, the source
   Python script, and any local data needed to reproduce the document. For a PDF
   request retain an editable DOCX or PPTX source alongside the PDF.
3. When results are on a selected server, retrieve only the required reports,
   figures and tables through the existing registered transfer tools into the
   project. Preserve original inputs and registered protected paths. Do not
   substitute new calculations for a selected fixed analysis workflow.
4. Default to a new revision (`report-v02.docx`, `slides-v02.pptx`, etc.) when
   continuing a conversation. Locate the actual previous file/source first;
   keep unrelated content and styling. Never silently replace the only copy of
   an existing document or edit the original source results.

## Managed document tools

1. Call `omaax_documents` with `action: "prepare"`. It returns JSON containing
   `ready`, an absolute `python` executable path and package versions. The managed
   packages include `python-docx`, `python-pptx`, Pillow and `pypdf`.
2. Write the generation/editing Python script inside the current project using
   the existing file tools. Run that script with the **returned Python path**
   through the normal `pwsh` or `bash` tool. Keep normal workspace permissions;
   do not widen them to make a document. On PowerShell use the call operator `&`
   followed by the quoted returned path, then `-E -s -B -X utf8 -u` and the quoted
   script path. On POSIX use the quoted returned path followed by the same flags
   and script path. These flags isolate the document interpreter from an active
   bioinformatics environment and keep its installation read-only.
3. For PDF output call `omaax_documents` with `action: "pdf"`, `input` pointing to
   the saved DOCX/PPTX, and `output` pointing to a new `.pdf` inside the project.
4. For page images call `omaax_documents` with `action: "preview"`, `input` pointing
   to DOCX/PPTX/PDF, and `output` pointing to a new preview directory inside the
   project. Optional `pages` and `dpi` restrict page selection and image size.

Use these supplied tools instead of running the Node CLI through the shell:
the tool prepares the private environment and converts documents without opening
the shell sandbox. Arbitrary generation scripts still run under the existing
shell restrictions. Do not use a system Python alias or install document packages
into the bioinformatics environment. Do not dump environment variables or secrets.

The first use may download the managed Python environment and document packages;
tell the user briefly that document components are being prepared. Do not claim
generation has finished while preparation is running. If preparation or
conversion fails, keep any successfully generated editable file, report the
specific failure, and retry only after addressing the cause. Do not ask the
user to install Microsoft Office or WPS as a prerequisite. The PDF engine is
bundled separately; `prepare.ready` confirms the Python environment, not a
successful conversion. Confirm PDF/preview success from the actual operation.
On Windows the engine also needs Microsoft Visual C++ v14 of the matching
architecture. If the operation reports that component missing, explain that
specific prerequisite and retain the editable files; do not report a PDF as
delivered or silently change system software.

## Word reports

Use `python-docx` for editable paragraphs, headings, tables, captions, page
settings and embedded pictures. A normal research report has a title, purpose,
inputs/method summary, findings with figures/tables, limitations and references
that are supported by the actual material. Adjust the structure to the request.

- Use heading styles and consistent spacing; avoid fake headings made of spaces.
- Set the page size, margins, header/footer and body font deliberately. Use a
  locally available Chinese-capable font for Chinese documents. Set both Latin
  and East Asian fonts on every used style (Normal, Title, Heading levels),
  table text and direct-format runs. Changing Normal alone leaves inherited
  heading fonts behind and can damage PDF text extraction order. Do not assume
  the default Calibri covers Chinese.
- Use real tables and editable text, not screenshots of all the text. Embed
  figures from local files with preserved aspect ratios and informative captions.
- Keep long tables readable with sensible column widths; repeat header rows
  when a table spans pages. Avoid leaving a caption alone on the next page.
- Load an existing file with `Document(path)` for revisions and change only the
  requested sections. Replacing `paragraph.text` destroys run formatting, so
  modify appropriate runs or deliberately rebuild only the affected paragraph.
  Inspect complex objects before editing; do not discard unsupported features
  merely to regenerate a document from scratch.

Example font setup (choose a font that actually exists on this machine):

```python
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

doc = Document()
style = doc.styles['Normal']
style.font.name = 'Microsoft YaHei'
style.font.size = Pt(11)
rpr = style.element.get_or_add_rPr()
fonts = rpr.find(qn('w:rFonts'))
if fonts is None:
    fonts = OxmlElement('w:rFonts')
    rpr.insert(0, fonts)
fonts.set(qn('w:eastAsia'), 'Microsoft YaHei')
```

## PowerPoint presentations

Use `python-pptx`; deliver an editable `.pptx`. Default to 16:9, a restrained
OMaaX palette and readable typography unless a user template says otherwise.
Use the requested number of slides; if unspecified, choose a concise deck that
covers the actual results. Reuse a supplied template with `Presentation(path)`.

- One main idea per slide; use short text with full explanations in speaker notes.
- Prefer native text, tables and simple shapes. Embed actual result figures
  using `add_picture()`; do not link to images outside the PPTX file.
- Keep images proportional and check bounding boxes against slide dimensions.
  Maintain consistent titles, margins, figure alignment and caption size.
- Set fonts explicitly, including a Chinese-capable font for Chinese text;
  do not rely on the machine-wide default font.
- For revisions open the preceding PPTX, identify the requested slide/shape and
  modify those objects. Preserve unrelated slides, charts, notes and images.
  When working from a saved generator, change only the requested content/layout
  and keep the script and source data in the project.
- A deck made entirely of slide-sized screenshots is not an editable PPTX.

## PDF, validation and delivery

1. Save the DOCX/PPTX first, then call `pdf` for a PDF copy. Never rename a Word
   or PowerPoint file to `.pdf`. A PDF exported from slides is a paginated deck,
   not an editable slide document; keep the PPTX when both are requested.
2. Call `preview` into a new revision-specific directory. Read its `images`,
   `pageCount` and `missingFonts` fields; replace missing fonts with installed
   equivalents before final delivery. Use the available
   image-viewing tools to inspect rendered pages/slides for clipped text, missing
   glyphs, stretched images and empty pages. Fix the source and regenerate into
   a new filename when required. If the current conversation has no image viewer,
   perform document/text/geometry checks and state that visual inspection was
   unavailable; do not falsely claim it was visually verified.
3. Verify output existence and nonzero size. Reopen DOCX/PPTX with its library;
   check expected headings/slides, embedded media and table values. Open PDFs
   with `pypdf.PdfReader` and check page count and extracted text. Match
   the output against the user's requested revisions.
4. Deliver each actual generated file with a clickable Markdown link using its
   full local absolute path. For paths containing spaces use `[report](<path>)`.
   Tell the user which project subfolder holds the files and offer concise next
   edits. Keep the editable source and generation script there so a follow-up
   such as “make slide 3 two figures side by side” can revise the existing work.
   Do not substitute a code block, a fictional download URL or an outline for
   the requested file. Do not reveal internal environment variables or tool names
   in the user-facing handoff unless troubleshooting was requested.
