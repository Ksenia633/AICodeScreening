package ru.aicode.screening

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper

class ScreeningDatabase(context: Context) : SQLiteOpenHelper(
    context,
    DATABASE_NAME,
    null,
    DATABASE_VERSION
) {
    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE candidates (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                repository TEXT NOT NULL,
                files_analyzed INTEGER NOT NULL,
                score INTEGER NOT NULL,
                summary TEXT NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL,
                file TEXT NOT NULL,
                lines TEXT NOT NULL,
                score INTEGER NOT NULL,
                reason TEXT NOT NULL,
                FOREIGN KEY(candidate_id) REFERENCES candidates(id)
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                FOREIGN KEY(candidate_id) REFERENCES candidates(id)
            )
            """.trimIndent()
        )
        seed(db)
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        db.execSQL("DROP TABLE IF EXISTS questions")
        db.execSQL("DROP TABLE IF EXISTS findings")
        db.execSQL("DROP TABLE IF EXISTS candidates")
        onCreate(db)
    }

    fun getCandidates(): List<Candidate> {
        val result = mutableListOf<Candidate>()
        readableDatabase.query(
            "candidates",
            null,
            null,
            null,
            null,
            null,
            "id ASC"
        ).use { cursor ->
            while (cursor.moveToNext()) {
                result += Candidate(
                    id = cursor.getInt(cursor.getColumnIndexOrThrow("id")),
                    name = cursor.getString(cursor.getColumnIndexOrThrow("name")),
                    role = cursor.getString(cursor.getColumnIndexOrThrow("role")),
                    repository = cursor.getString(cursor.getColumnIndexOrThrow("repository")),
                    filesAnalyzed = cursor.getInt(cursor.getColumnIndexOrThrow("files_analyzed")),
                    score = cursor.getInt(cursor.getColumnIndexOrThrow("score")),
                    summary = cursor.getString(cursor.getColumnIndexOrThrow("summary"))
                )
            }
        }
        return result
    }

    fun getAnalysis(candidateId: Int): Analysis {
        val candidate = getCandidates().first { it.id == candidateId }
        val findings = mutableListOf<Finding>()
        readableDatabase.query(
            "findings",
            arrayOf("file", "lines", "score", "reason"),
            "candidate_id = ?",
            arrayOf(candidateId.toString()),
            null,
            null,
            "score DESC"
        ).use { cursor ->
            while (cursor.moveToNext()) {
                findings += Finding(
                    file = cursor.getString(0),
                    lines = cursor.getString(1),
                    score = cursor.getInt(2),
                    reason = cursor.getString(3)
                )
            }
        }
        val questions = mutableListOf<String>()
        readableDatabase.query(
            "questions",
            arrayOf("question"),
            "candidate_id = ?",
            arrayOf(candidateId.toString()),
            null,
            null,
            "id ASC"
        ).use { cursor ->
            while (cursor.moveToNext()) questions += cursor.getString(0)
        }
        return Analysis(
            repository = candidate.repository,
            filesAnalyzed = candidate.filesAnalyzed,
            findings = findings,
            questions = questions
        )
    }

    private fun seed(db: SQLiteDatabase) {
        insertCandidate(db, 1, "Алексей Иванов", "Android Developer", "Ksenia633/mobile-demo", 18, 72, "Есть несколько участков, которые стоит обсудить на техническом интервью.")
        insertCandidate(db, 2, "Мария Петрова", "Backend Developer", "Ksenia633/backend-demo", 24, 38, "Основная часть кода выглядит последовательно; для интервью подготовлены уточняющие вопросы.")
        insertCandidate(db, 3, "Дмитрий Смирнов", "Kotlin Developer", "Ksenia633/kotlin-demo", 15, 61, "Найдены отдельные эвристические сигналы для дополнительной проверки понимания кода.")

        insertFinding(db, 1, "app/src/main/java/Repository.kt", "18–47", 82, "высокая средняя длина строк; много однотипных общих комментариев")
        insertFinding(db, 1, "app/src/main/java/NetworkClient.kt", "1–40", 62, "нет очевидных признаков тестовой проверки")
        insertFinding(db, 2, "src/services/UserService.py", "12–38", 43, "шаблонные маркеры TODO/FIXME")
        insertFinding(db, 3, "src/main/kotlin/Parser.kt", "25–64", 74, "высокая средняя длина строк")

        insertQuestion(db, 1, "Объясните своими словами, как работает Repository.kt. Почему выбран именно такой подход?")
        insertQuestion(db, 1, "Какие альтернативы работе с данными вы рассматривали и какие у них компромиссы?")
        insertQuestion(db, 1, "Что произойдет при потере сети во время выполнения NetworkClient.kt?")
        insertQuestion(db, 2, "Почему UserService.py разделен именно на эти операции? Какие есть альтернативы?")
        insertQuestion(db, 2, "Как бы вы покрыли UserService.py тестами?")
        insertQuestion(db, 3, "Объясните алгоритм Parser.kt без просмотра исходного кода.")
        insertQuestion(db, 3, "Какие ошибки и граничные случаи нужно обработать в Parser.kt?")
    }

    private fun insertCandidate(db: SQLiteDatabase, id: Int, name: String, role: String, repository: String, files: Int, score: Int, summary: String) {
        db.insert("candidates", null, ContentValues().apply {
            put("id", id)
            put("name", name)
            put("role", role)
            put("repository", repository)
            put("files_analyzed", files)
            put("score", score)
            put("summary", summary)
        })
    }

    private fun insertFinding(db: SQLiteDatabase, candidateId: Int, file: String, lines: String, score: Int, reason: String) {
        db.insert("findings", null, ContentValues().apply {
            put("candidate_id", candidateId)
            put("file", file)
            put("lines", lines)
            put("score", score)
            put("reason", reason)
        })
    }

    private fun insertQuestion(db: SQLiteDatabase, candidateId: Int, question: String) {
        db.insert("questions", null, ContentValues().apply {
            put("candidate_id", candidateId)
            put("question", question)
        })
    }

    companion object {
        private const val DATABASE_NAME = "ai_screening.db"
        private const val DATABASE_VERSION = 1
    }
}

data class Candidate(
    val id: Int,
    val name: String,
    val role: String,
    val repository: String,
    val filesAnalyzed: Int,
    val score: Int,
    val summary: String
)
