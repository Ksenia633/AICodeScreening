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
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

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

@Composable
private fun ScreeningApp() {
    val context = LocalContext.current
    val database = remember { ScreeningDatabase(context.applicationContext) }
    var candidates by remember { mutableStateOf<List<Candidate>>(emptyList()) }
    var selectedCandidate by remember { mutableStateOf<Candidate?>(null) }
    var analysis by remember { mutableStateOf<Analysis?>(null) }

    LaunchedEffect(Unit) {
        candidates = withContext(Dispatchers.IO) { database.getCandidates() }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("AI Code Screening", fontWeight = FontWeight.Bold)
                        Text("Автономный скрининг", style = MaterialTheme.typography.labelSmall)
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface
                )
            )
        }
    ) { innerPadding ->
        if (analysis == null) {
            CandidateList(
                candidates = candidates,
                modifier = Modifier.padding(innerPadding),
                onCandidateClick = { candidate ->
                    selectedCandidate = candidate
                    analysis = database.getAnalysis(candidate.id)
                }
            )
        } else {
            AnalysisScreen(
                candidate = selectedCandidate,
                analysis = analysis!!,
                modifier = Modifier.padding(innerPadding),
                onBack = {
                    analysis = null
                    selectedCandidate = null
                }
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
        modifier = modifier.fillMaxSize().padding(horizontal = 16.dp),
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
                        "Выберите кандидата из локальной базы. Интернет, GitHub URL и запуск Python-сервера для демонстрации не требуются.",
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
        if (candidates.isEmpty()) {
            item {
                Card(Modifier.fillMaxWidth()) {
                    Text("Загружаем локальную базу…", Modifier.padding(18.dp))
                }
            }
        } else {
            items(candidates, key = { it.id }) { candidate ->
                CandidateCard(candidate, onClick = { onCandidateClick(candidate) })
            }
        }
        item {
            Spacer(Modifier.height(8.dp))
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(18.dp)) {
                    Text("О режиме работы", fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "Демонстрационные результаты хранятся внутри приложения в SQLite-базе. Анализ является вспомогательным сигналом и не доказывает использование AI-кода.",
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
                    Text(candidate.name, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
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
    candidate: Candidate?,
    analysis: Analysis,
    modifier: Modifier = Modifier,
    onBack: () -> Unit
) {
    LazyColumn(
        modifier = modifier.fillMaxSize().padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        item {
            Spacer(Modifier.height(4.dp))
            OutlinedButton(
                onClick = onBack,
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(14.dp)
            ) { Text("← К списку кандидатов") }
        }
        item {
            Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(20.dp)) {
                Column(Modifier.padding(20.dp)) {
                    Text("Результат анализа", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(4.dp))
                    candidate?.let {
                        Text(it.name, fontWeight = FontWeight.SemiBold)
                        Text(it.role, style = MaterialTheme.typography.bodySmall)
                    }
                    Text(analysis.repository, style = MaterialTheme.typography.bodyMedium)
                    Spacer(Modifier.height(16.dp))
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Metric("Сигнал", "${analysis.suspicionScore}%")
                        Metric("Файлов", analysis.filesAnalyzed.toString())
                        Metric("Участков", analysis.findings.size.toString())
                    }
                    Spacer(Modifier.height(12.dp))
                    Text(
                        "Сигнал отражает признаки, которые стоит дополнительно обсудить с кандидатом.",
                        style = MaterialTheme.typography.bodySmall
                    )
                }
            }
        }
        item {
            Text("Подозрительные участки", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
        }
        if (analysis.findings.isEmpty()) {
            item {
                Card {
                    Text("Подозрительных участков не найдено. Это не доказывает отсутствие AI-кода.", Modifier.padding(16.dp))
                }
            }
        } else {
            items(analysis.findings) { finding -> FindingCard(finding) }
        }
        item {
            Text("Вопросы для собеседования", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
        }
        items(analysis.questions) { question -> QuestionCard(question) }
        item {
            Spacer(Modifier.height(4.dp))
            OutlinedButton(
                onClick = onBack,
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(14.dp)
            ) { Text("Проверить другого кандидата") }
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
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Column(Modifier.weight(1f)) {
                    Text(finding.file, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
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
                    .background(MaterialTheme.colorScheme.secondaryContainer, RoundedCornerShape(9.dp)),
                contentAlignment = Alignment.Center
            ) {
                Text("?", fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.size(12.dp))
            Text(question, style = MaterialTheme.typography.bodyLarge)
        }
    }
}
