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
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

data class Finding(
    val file: String,
    val lines: String,
    val score: Int,
    val reason: String
)

data class Analysis(
    val repository: String,
    val filesAnalyzed: Int,
    val findings: List<Finding>,
    val questions: List<String>
) {
    val suspicionScore: Int
        get() = if (findings.isEmpty()) 0 else findings.map { it.score }.average().toInt().coerceIn(0, 100)
}

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

    fun startAnalysis(repositoryUrl: String, onResult: (Result<Analysis>) -> Unit) {
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    val url = URL("http://10.0.2.2:8000/api/analyze")
                    val connection = (url.openConnection() as HttpURLConnection).apply {
                        requestMethod = "POST"
                        connectTimeout = 10_000
                        readTimeout = 60_000
                        doOutput = true
                        setRequestProperty("Content-Type", "application/json")
                    }
                    connection.outputStream.use { out ->
                        out.write(
                            JSONObject()
                                .put("repository_url", repositoryUrl)
                                .toString()
                                .toByteArray()
                        )
                    }
                    if (connection.responseCode !in 200..299) {
                        error("Backend вернул ошибку HTTP ${connection.responseCode}")
                    }
                    val body = connection.inputStream.bufferedReader().use { it.readText() }
                    parseAnalysis(body)
                }
            }
            onResult(result)
        }
    }

    private fun parseAnalysis(body: String): Analysis {
        val root = JSONObject(body)
        val findingArray = root.optJSONArray("findings")
        val findings = buildList {
            if (findingArray != null) {
                for (i in 0 until findingArray.length()) {
                    val item = findingArray.getJSONObject(i)
                    add(
                        Finding(
                            file = item.optString("file", "Unknown file"),
                            lines = item.optString("lines", "—"),
                            score = item.optInt("score", 0),
                            reason = item.optString("reason", "Подозрительные признаки требуют дополнительной проверки.")
                        )
                    )
                }
            }
        }
        val questionArray = root.optJSONArray("questions")
        val questions = buildList {
            if (questionArray != null) {
                for (i in 0 until questionArray.length()) add(questionArray.getString(i))
            }
        }
        return Analysis(
            repository = root.optString("repository", "GitHub repository"),
            filesAnalyzed = root.optInt("files_analyzed", 0),
            findings = findings,
            questions = questions
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ScreeningApp() {
    val activity = LocalContext.current as? MainActivity
    var repository by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var analysis by remember { mutableStateOf<Analysis?>(null) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("AI Code Screening", fontWeight = FontWeight.Bold)
                        Text("Инструмент для HR", style = MaterialTheme.typography.labelSmall)
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.surface)
            )
        }
    ) { innerPadding ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(innerPadding).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            item {
                Spacer(Modifier.height(4.dp))
                HeroCard()
            }
            item {
                OutlinedTextField(
                    value = repository,
                    onValueChange = { repository = it; error = null },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("GitHub repository") },
                    placeholder = { Text("https://github.com/owner/project") },
                    supportingText = { Text("Используйте публичный репозиторий") },
                    singleLine = true,
                    shape = RoundedCornerShape(14.dp)
                )
            }
            item {
                Button(
                    onClick = {
                        val url = repository.trim()
                        if (activity != null) {
                            loading = true
                            error = null
                            analysis = null
                            activity.startAnalysis(url) { result ->
                                loading = false
                                result.onSuccess { analysis = it }
                                    .onFailure { error = it.message ?: "Неизвестная ошибка" }
                            }
                        } else {
                            error = "Не удалось получить Android Activity"
                        }
                    },
                    enabled = repository.isNotBlank() && !loading,
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(14.dp)
                ) {
                    if (loading) {
                        CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                        Spacer(Modifier.size(8.dp))
                    }
                    Text(if (loading) "Анализируем…" else "Начать анализ")
                }
            }
            if (analysis == null && !loading) item { HowItWorksCard() }

            error?.let { message ->
                item {
                    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)) {
                        Text(message, modifier = Modifier.padding(16.dp), color = MaterialTheme.colorScheme.onErrorContainer)
                    }
                }
            }

            analysis?.let { result ->
                item { SummaryCard(result) }
                item { Text("Подозрительные участки", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold) }
                if (result.findings.isEmpty()) {
                    item {
                        Card {
                            Text(
                                "Подозрительных участков не найдено. Это не доказывает отсутствие AI-кода — результат является вспомогательным сигналом.",
                                modifier = Modifier.padding(16.dp)
                            )
                        }
                    }
                } else {
                    items(result.findings) { finding -> FindingCard(finding) }
                }
                item { Text("Вопросы для собеседования", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold) }
                if (result.questions.isEmpty()) {
                    item { Card { Text("Вопросы пока не сформированы.", Modifier.padding(16.dp)) } }
                } else {
                    items(result.questions) { question -> QuestionCard(question) }
                }
                item {
                    OutlinedButton(
                        onClick = { analysis = null; error = null; loading = false },
                        modifier = Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(14.dp)
                    ) { Text("Проверить другой проект") }
                }
                item { Spacer(Modifier.height(12.dp)) }
            }
        }
    }
}

@Composable
private fun HeroCard() {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(22.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)
    ) {
        Column(Modifier.padding(22.dp)) {
            Text("Проверка опыта разработчика", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            Text("Анализируйте публичные GitHub-проекты и получайте вопросы для технического скрининга.", style = MaterialTheme.typography.bodyLarge)
        }
    }
}

@Composable
private fun HowItWorksCard() {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(18.dp)) {
            Text("Как это работает", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(12.dp))
            Step("01", "GitHub", "Добавьте ссылку на публичный репозиторий")
            Step("02", "Анализ", "Система проверит структуру и исходный код")
            Step("03", "Скрининг", "Получите участки для проверки и вопросы кандидату")
            Spacer(Modifier.height(10.dp))
            HorizontalDivider()
            Spacer(Modifier.height(10.dp))
            Text("Важно: результат не является доказательством использования нейросети. Окончательное решение принимает HR.", style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun Step(number: String, title: String, description: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 5.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(
            modifier = Modifier.size(34.dp).background(MaterialTheme.colorScheme.secondaryContainer, RoundedCornerShape(10.dp)),
            contentAlignment = Alignment.Center
        ) { Text(number, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold) }
        Spacer(Modifier.size(12.dp))
        Column { Text(title, fontWeight = FontWeight.SemiBold); Text(description, style = MaterialTheme.typography.bodySmall) }
    }
}

@Composable
private fun SummaryCard(result: Analysis) {
    Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(20.dp)) {
        Column(Modifier.padding(20.dp)) {
            Text("Результат анализа", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(4.dp))
            Text(result.repository, style = MaterialTheme.typography.bodyMedium)
            Spacer(Modifier.height(18.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Metric("Сигнал", "${result.suspicionScore}%")
                Metric("Файлов", result.filesAnalyzed.toString())
                Metric("Участков", result.findings.size.toString())
            }
            Spacer(Modifier.height(14.dp))
            Text("Сигнал отражает наличие признаков, требующих дополнительной проверки.", style = MaterialTheme.typography.bodySmall)
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
            Text("?", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleLarge)
            Spacer(Modifier.size(12.dp))
            Text(question, style = MaterialTheme.typography.bodyLarge)
        }
    }
}
