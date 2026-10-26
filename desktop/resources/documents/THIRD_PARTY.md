# OMaaX document skill provenance

The `skills/omaax-documents/SKILL.md` guide is an original OMaaX integration guide,
distributed under the repository's MIT license. No DeepSeek Harness core or
newer runtime source is copied into this skill. The guide uses the existing
stable Harness filesystem-skill interface and the local OMaaX document runner.

The implementation approach was evaluated against the public DeepSeek Harness
Office skill and LibreOffice Kit projects:

- https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/skill/skill-office
- https://github.com/deepseek-ai/dsh-libreoffice-kit

Runtime package attribution and redistribution details are listed separately in
`THIRD_PARTY_NOTICES.md`. The skill does not bundle Microsoft Office or WPS and
does not require either application for generation or conversion.
