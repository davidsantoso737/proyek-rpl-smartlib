SOURCE OF TRUTH HIERARCHY

1. Hasil Observasi dan Analisis Kebutuhan Sistem
   = SUMBER OTORITATIF UTAMA
   Menentukan:
   - aktor
   - hak akses
   - business rules
   - functional requirements
   - perilaku sistem
   - batasan sistem

2. Use Case
   = REFERENSI INTERAKSI AKTOR
   Digunakan untuk:
   - memahami daftar use case
   - melihat hubungan aktor dengan fungsi
   - membantu memahami cakupan fitur

   Jika Use Case bertentangan dengan Analisis Kebutuhan,
   JANGAN mengubah requirement berdasarkan interpretasi Use Case.
   Laporkan konflik dan gunakan Analisis Kebutuhan sebagai
   sumber otoritatif.

3. Activity Diagram
   = REFERENSI ALUR PROSES
   Digunakan untuk memahami:
   - urutan aktivitas
   - decision point
   - alur proses

   Jangan menciptakan business rule baru dari diagram.

4. ERD
   = REFERENSI STRUKTUR DATA
   Digunakan untuk:
   - entity
   - attribute
   - PK
   - FK
   - junction table

   Jangan menebak cardinality, business rule, atau perilaku
   sistem yang tidak eksplisit.

5. Master Development Roadmap
   = REFERENSI DEKOMPOSISI IMPLEMENTASI
   Menentukan:
   - urutan phase
   - dependency
   - checkpoint
   - prompt implementasi

   Roadmap tidak menggantikan requirement.

6. Repository
   = SUMBER KONDISI TEKNIS AKTUAL
   Sebelum implementasi, selalu inspect repository.

7. USE CASE INTERPRETATION RULE

    Use Case Diagram/Document is a supporting reference, not the
    primary source of truth for actor responsibilities.

    The authoritative definition of actor responsibilities is the
    Hasil Observasi dan Analisis Kebutuhan Sistem, especially:

    - Actor definitions
    - Business Rules (BR)
    - Functional Requirements (FR)

    If the AI interpretation of the Use Case conflicts with the
    Analisis Kebutuhan Sistem:

    1. Do not invent or infer a new actor responsibility.
    2. Do not assign AI Assistant or AI Recommendation tasks to
    Pustakawan unless the authoritative requirement explicitly
    states so.
    3. Do not modify the requirement to match the Use Case.
    4. Report the conflict.
    5. Follow the Analisis Kebutuhan Sistem unless the user explicitly
    decides that the requirement document itself must be changed.

    AI ACTOR INTERPRETATION RULE

    The AI Assistant and AI Recommendation are system capabilities.

    The user interacts with AI through the system.

    Do not interpret AI Assistant or AI Recommendation as an
    administrative task performed by Pustakawan unless explicitly
    specified by the authoritative requirement.

    Pustakawan remains responsible for administrative library
    operations and decisions that require librarian authorization.