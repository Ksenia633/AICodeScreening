package ru.aicode.screening

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    ScreeningApp()
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ScreeningApp() {
    val candidates = remember { demoCandidates }
    var selectedCandidate by remember { mutableStateOf<Candidate?>(null) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("AI Code Screening", fontWeight = FontWeight.Bold)
                        Text("Демонстрационный режим", style = MaterialTheme.typography.labelSmall)
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface
                )
            )
        }
    ) { innerPadding ->
        if (selectedCandidate == null) {
            CandidateList(
                candidates = candidates,
                modifier = Modifier.padding(innerPadding),
                onCandidateClick = { selectedCandidate = it }
            )
        } else {
            AnalysisScreen(
                candidate = selectedCandidate!!,
                modifier = Modifier.padding(innerPadding),
                onBack = { selectedCandidate = null }
            )
        }
    }
}

@Composable
private fun CandidateList(
    candidates: List<Candidate>,
    modifier: Modifier = Modifier,
    onCandidateClick: (Candidate) -> Unit
) {
    LazyColumn(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        item {
            Spacer(Modifier.height(4.dp))
            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(22.dp),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.primaryContainer
                )
            ) {
                Column(Modifier.padding(22.dp)) {
                    Text(
                        "Проверка кандидатов",
                        style = MaterialTheme.typography.headlineSmall,
                        fontWeight = FontWeight.Bold
                    )
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Выберите кандидата, чтобы посмотреть результат анализа и вопросы для технического интервью.",
                        style = MaterialTheme.typography.bodyLarge
                    )
                }
            }
        }

        item {
            Text(
                "Кандидаты",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold
            )
        }

        items(candidates, key = { it.id }) { candidate ->
            CandidateCard(candidate) { onCandidateClick(candidate) }
        }

        item {
            Spacer(Modifier.height(8.dp))
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(18.dp)) {
                    Text("О режиме работы", fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "В этой версии данные встроены непосредственно в приложение. SQLite, интернет, GitHub URL и Python-сервер для демонстрации не нужны.",
                        style = MaterialTheme.typography.bodySmall
                    )
                }
            }
            Spacer(Modifier.height(12.dp))
        }
    }
}

@Composable
private fun CandidateCard(candidate: Candidate, onClick: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        onClick = onClick,
        shape = RoundedCornerShape(18.dp)
    ) {
        Column(Modifier.padding(18.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.Top
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        candidate.name,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold
                    )
                    Text(candidate.role, style = MaterialTheme.typography.bodyMedium)
                }
                Text("${candidate.score}%", fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.height(10.dp))
            Text(candidate.repository, style = MaterialTheme.typography.labelMedium)
            Spacer(Modifier.height(8.dp))
            Text(candidate.summary, style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun AnalysisScreen(
    candidate: Candidate,
    modifier: Modifier = Modifier,
    onBack: () -> Unit
) {
    val analysis = candidate.analysis

    LazyColumn(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        item {
            Spacer(Modifier.height(4.dp))
            OutlinedButton(
                onClick = onBack,
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(14.dp)
            ) {
                Text("← К списку кандидатов")
            }
        }

        item {
            Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(20.dp)) {
                Column(Modifier.padding(20.dp)) {
                    Text(
                        "Результат анализа",
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(candidate.name, fontWeight = FontWeight.SemiBold)
                    Text(candidate.role, style = MaterialTheme.typography.bodySmall)
                    Text(analysis.repository, style = MaterialTheme.typography.bodyMedium)
                    Spacer(Modifier.height(16.dp))
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Metric("Сигнал", "${candidate.score}%")
                        Metric("Файлов", analysis.filesAnalyzed.toString())
                        Metric("Участков", analysis.findings.size.toString())
                    }
                    Spacer(Modifier.height(12.dp))
                    Text(
                        "Сигнал является вспомогательной эвристикой и не доказывает использование AI-кода.",
                        style = MaterialTheme.typography.bodySmall
                    )
                }
            }
        }

        item {
            Text(
                "Подозрительные участки",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold
            )
        }

        if (analysis.findings.isEmpty()) {
            item {
                Card {
                    Text(
                        "Подозрительных участков не найдено.",
                        Modifier.padding(16.dp)
                    )
                }
            }
        } else {
            items(analysis.findings) { finding -> FindingCard(finding) }
        }

        item {
            Text(
                "Вопросы для собеседования",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold
            )
        }

        items(analysis.questions) { question -> QuestionCard(question) }

        item {
            Spacer(Modifier.height(4.dp))
            OutlinedButton(
                onClick = onBack,
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(14.dp)
            ) {
                Text("Проверить другого кандидата")
            }
            Spacer(Modifier.height(12.dp))
        }
    }
}

@Composable
private fun Metric(label: String, value: String) {
    Column {
        Text(value, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
        Text(label, style = MaterialTheme.typography.labelMedium)
    }
}

@Composable
private fun FindingCard(finding: Finding) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        finding.file,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold
                    )
                    Text("Строки ${finding.lines}", style = MaterialTheme.typography.labelMedium)
                }
                Text("${finding.score}%", fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.height(10.dp))
            Text(finding.reason, style = MaterialTheme.typography.bodyMedium)
        }
    }
}

@Composable
private fun QuestionCard(question: String) {
    Card(Modifier.fillMaxWidth()) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.Top) {
            Box(
                modifier = Modifier
                    .size(30.dp)
                    .background(
                        MaterialTheme.colorScheme.secondaryContainer,
                        RoundedCornerShape(9.dp)
                    ),
                contentAlignment = Alignment.Center
            ) {
                Text("?", fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.size(12.dp))
            Text(question, style = MaterialTheme.typography.bodyLarge)
        }
    }
}

data class Candidate(
    val id: Int,
    val name: String,
    val role: String,
    val repository: String,
    val score: Int,
    val summary: String,
    val analysis: Analysis
)

data class Analysis(
    val repository: String,
    val filesAnalyzed: Int,
    val findings: List<Finding>,
    val questions: List<String>
)

data class Finding(
    val file: String,
    val lines: String,
    val score: Int,
    val reason: String
)

private val demoCandidates = listOf(
    Candidate(
        id = 1,
        name = "Алексей Иванов",
        role = "Android Developer",
        repository = "Ksenia633/mobile-demo",
        score = 72,
        summary = "Есть несколько участков, которые стоит обсудить на техническом интервью.",
        analysis = Analysis(
            repository = "Ksenia633/mobile-demo",
            filesAnalyzed = 18,
            findings = listOf(
                Finding(
                    file = "app/src/main/java/Repository.kt",
                    lines = "18–47",
                    score = 82,
                    reason = "Высокая средняя длина строк; много однотипных общих комментариев."
                ),
                Finding(
                    file = "app/src/main/java/NetworkClient.kt",
                    lines = "1–40",
                    score = 62,
                    reason = "Нет очевидных признаков тестовой проверки."
                )
            ),
            questions = listOf(
                "Объясните своими словами, как работает Repository.kt. Почему выбран именно такой подход?",
                "Какие альтернативы работе с данными вы рассматривали и какие у них компромиссы?",
                "Что произойдет при потере сети во время выполнения NetworkClient.kt?"
            )
        )
    ),
    Candidate(
        id = 2,
        name = "Мария Петрова",
        role = "Backend Developer",
        repository = "Ksenia633/backend-demo",
        score = 38,
        summary = "Основная часть кода выглядит последовательно; для интервью подготовлены уточняющие вопросы.",
        analysis = Analysis(
            repository = "Ksenia633/backend-demo",
            filesAnalyzed = 24,
            findings = listOf(
                Finding(
                    file = "src/services/UserService.py",
                    lines = "12–38",
                    score = 43,
                    reason = "Шаблонные маркеры TODO/FIXME."
                )
            ),
            questions = listOf(
                "Почему UserService.py разделен именно на эти операции? Какие есть альтернативы?",
                "Как бы вы покрыли UserService.py тестами?"
            )
        )
    ),
    Candidate(
        id = 3,
        name = "Дмитрий Смирнов",
        role = "Kotlin Developer",
        repository = "Ksenia633/kotlin-demo",
        score = 61,
        summary = "Найдены отдельные эвристические сигналы для дополнительной проверки понимания кода.",
        analysis = Analysis(
            repository = "Ksenia633/kotlin-demo",
            filesAnalyzed = 15,
            findings = listOf(
                Finding(
                    file = "src/main/kotlin/Parser.kt",
                    lines = "25–64",
                    score = 74,
                    reason = "Высокая средняя длина строк."
                )
            ),
            questions = listOf(
                "Объясните алгоритм Parser.kt без просмотра исходного кода.",
                "Какие ошибки и граничные случаи нужно обработать в Parser.kt?"
            )
        )
    )
)
