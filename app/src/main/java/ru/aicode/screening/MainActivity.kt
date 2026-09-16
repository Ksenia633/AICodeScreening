package ru.aicode.screening

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

private data class Finding(
    val file: String,
    val lines: String,
    val score: Int,
    val reason: String
)

private data class Analysis(
    val repository: String,
    val filesAnalyzed: Int,
    val findings: List<Finding>,
    val questions: List<String>
)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    ScreeningScreen()
                }
            }
        }
    }

    private fun analyze(repositoryUrl: String, onResult: (Result<Analysis>) -> Unit) {
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    val url = URL("http://10.0.2.2:8000/api/analyze")
                    val connection = (url.openConnection() as HttpURLConnection).apply {
                        requestMethod = "POST"
                        connectTimeout = 10000
                        readTimeout = 60000
                        doOutput = true
                        setRequestProperty("Content-Type", "application/json")
                    }
                    connection.outputStream.use { out ->
                        out.write(JSONObject().put("repository_url", repositoryUrl).toString().toByteArray())
                    }
                    if (connection.responseCode !in 200..299) {
                        error("Backend error: HTTP ${connection.responseCode}")
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
        val findingArray = root.getJSONArray("findings")
        val findings = buildList {
            for (i in 0 until findingArray.length()) {
                val item = findingArray.getJSONObject(i)
                add(
                    Finding(
                        file = item.getString("file"),
                        lines = item.getString("lines"),
                        score = item.getInt("score"),
                        reason = item.getString("reason")
                    )
                )
            }
        }
        val questionArray = root.getJSONArray("questions")
        val questions = buildList {
            for (i in 0 until questionArray.length()) add(questionArray.getString(i))
        }
        return Analysis(
            repository = root.getString("repository"),
            filesAnalyzed = root.getInt("files_analyzed"),
            findings = findings,
            questions = questions
        )
    }

    @androidx.compose.runtime.Composable
    private fun ScreeningScreen() {
        var repository by remember { mutableStateOf("") }
        var loading by remember { mutableStateOf(false) }
        var error by remember { mutableStateOf<String?>(null) }
        var analysis by remember { mutableStateOf<Analysis?>(null) }

        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            item {
                Text("AI Code Screening", style = MaterialTheme.typography.headlineMedium)
                Spacer(Modifier.height(4.dp))
                Text(
                    "Проверка GitHub-проектов кандидата для подготовки технического скрининга.",
                    style = MaterialTheme.typography.bodyMedium
                )
                Spacer(Modifier.height(16.dp))
                OutlinedTextField(
                    value = repository,
                    onValueChange = { repository = it },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("GitHub repository") },
                    placeholder = { Text("https://github.com/owner/project") },
                    singleLine = true
                )
                Spacer(Modifier.height(8.dp))
                Button(
                    onClick = {
                        loading = true
                        error = null
                        analysis = null
                        analyze(repository.trim()) {
                            loading = false
                            it.onSuccess { analysis = it }
                                .onFailure { error = it.message ?: "Неизвестная ошибка" }
                        }
                    },
                    enabled = repository.isNotBlank() && !loading,
                    modifier = Modifier.fillMaxWidth()
                ) { Text(if (loading) "Анализируем…" else "Проверить проект") }
                if (loading) {
                    Spacer(Modifier.height(8.dp))
                    CircularProgressIndicator()
                }
                error?.let {
                    Spacer(Modifier.height(8.dp))
                    Text(it, color = MaterialTheme.colorScheme.error)
                }
            }

            analysis?.let { result ->
                item {
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp)) {
                            Text(result.repository, style = MaterialTheme.typography.titleLarge)
                            Text("Файлов проанализировано: ${result.filesAnalyzed}")
                            Spacer(Modifier.height(8.dp))
                            Text(
                                "Важно: показатель отражает подозрительные признаки кода, а не доказывает использование нейросети.",
                                style = MaterialTheme.typography.bodySmall
                            )
                        }
                    }
                }
                item {
                    Text("Подозрительные участки", style = MaterialTheme.typography.titleLarge)
                }
                items(result.findings) { finding ->
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp)) {
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                                Text(finding.file, style = MaterialTheme.typography.titleMedium)
                                Text("${finding.score}%")
                            }
                            Text("Строки: ${finding.lines}")
                            Spacer(Modifier.height(4.dp))
                            Text(finding.reason)
                        }
                    }
                }
                item {
                    Spacer(Modifier.height(4.dp))
                    Text("Вопросы для собеседования", style = MaterialTheme.typography.titleLarge)
                }
                items(result.questions) { question ->
                    Card(Modifier.fillMaxWidth()) {
                        Text(question, modifier = Modifier.padding(16.dp))
                    }
                }
            }
        }
    }
}
