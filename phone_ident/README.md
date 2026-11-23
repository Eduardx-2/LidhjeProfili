# Identificación de teléfonos

El cálculo usa la biblioteca estándar de Python. El extractor usa las dependencias
de `requirements.txt`. Desde `Documentos/vsco`:

```sh
python3 vsco.py
python3 -m unittest discover -v
```

```python
from phone_ident import identify_phone

result = identify_phone(
    make=["Apple", "Samsung", "Canon"],
    model=["iPhone 14", "SM-S918B", "EOS R5"],
    dates=["2026-01-01", "2026-01-02", "2026-01-03"],  # opcional
)
```

`make[i]` y `model[i]` deben corresponder a la misma imagen. Se admiten cadenas
y `None`; las listas de distinta longitud se rechazan, porque sin esa relación
no es posible asociar marca y modelo. No pasar listas anidadas como `["VSCO"]`.
La entrada pública es `from phone_ident import identify_phone`.

La salida separa los objetos `camera`, `phone` y `unknown`. `phone` contiene
conteos Android/iPhone, modelos observados con sus puntuaciones, candidatos con máxima
puntuación y probabilidades cuando se configura Bayes. Los empates se conservan.
`preferred_models` contiene los nombres con mayor `score`, no el valor numérico
ni una confirmación de uso actual. No se incluyen los bloques `indicators` ni
`warnings`; las entradas inválidas siguen produciendo `ValueError` o `TypeError`.
No se deduce Android solamente de la marca Samsung/Sony, ni iPhone solamente
de Apple. iPad, plataformas genéricas y modelos fuera del catálogo quedan
desconocidos. Las reglas no son un catálogo exhaustivo ni validan autenticidad
EXIF; pueden ampliarse en `identify_device` sin cambiar el cálculo.

## Puntuación S = w1*C + w2*T + w3*D

La analogía de consumo/sabor/dulces se adapta explícitamente a dispositivos.
Para cada modelo candidato:

- **C**: imágenes del candidato / total de imágenes de teléfonos reconocidos.
- **T**: días de captura distintos del candidato / días distintos de todos los
  teléfonos reconocidos. Mide persistencia entre días observados, no antigüedad
  ni uso reciente. Requiere fechas válidas para todas las imágenes de teléfono.
- **D**: fracción de imágenes del candidato con un modelo específico, por ejemplo
  `iPhone 14`, frente a un nombre genérico como `iPhone`.

Todos los indicadores disponibles están entre 0 y 1. Los pesos predeterminados
`(0.5, 0.3, 0.2)` son una elección heurística configurable, no pesos aprendidos.
Se normalizan para sumar 1. Cuando falta T, se elimina su peso y se renormalizan
C/D. Si no queda ningún peso activo, la puntuación es `null`. Sin evidencia
telefónica tampoco se produce una puntuación ni un candidato preferido.

Fechas aceptadas: `date`, `datetime`, cadenas `YYYY-MM-DD` o milisegundos Unix
(convertidos a día UTC). Para `datetime` se utiliza el día de la zona suministrada;
conviene usar una sola zona para todo el conjunto. No se usa el orden de la lista
como sustituto del tiempo ni la fecha de subida como fecha de captura.
C y D pueden estar correlacionados; esta puntuación no es una probabilidad.

## Bayes

Hipótesis H: SO principal del usuario, restringido a ANDROID o IPHONE. Evidencia E:
SO observado en cada imagen cuyo teléfono se reconoce. Con independencia
condicional entre imágenes:

```text
P(H | E) ∝ P(H) * product_s P(SO observado=s | H) ** conteo(s)
```

Se calcula en logaritmos y se normaliza de forma estable. Una cámara o una imagen
desconocida no aporta evidencia de SO. Sin imágenes de teléfono, el resultado
es el prior, identificado por `probability_status = prior_only`.

No se inventan verosimilitudes: sin un `BayesModel`, `probability` es `null`.
Los priors son uniformes si se omiten al construir ese modelo; es un supuesto
explícito. Se pueden proporcionar tablas con filas que sumen 1:

```python
from phone_ident import BayesModel, PhoneData

# SOLO EJEMPLO SINTÉTICO; estos números no se deducen de data.json.
bayes = BayesModel(
    priors={"ANDROID": 0.5, "IPHONE": 0.5},
    likelihoods={
        "ANDROID": {"ANDROID": 0.8, "IPHONE": 0.2},
        "IPHONE": {"ANDROID": 0.2, "IPHONE": 0.8},
    },
)
result = PhoneData(["Apple"], ["iPhone 14"], bayes=bayes).identify_checkfon()
# Bajo estos supuestos, P(IPHONE | evidencia) = 0.8.
```

También se puede usar `BayesModel.fit(samples, alpha=1.0)`, donde cada muestra
es un perfil etiquetado `{label: "ANDROID"|"IPHONE", make: [...], model: [...]}`.
Aprende priors por número de perfiles y verosimilitudes por número de imágenes,
con suavizado de Laplace. Exige evidencia en ambas clases. La etiqueta debe
proceder de una verificación externa; deducirla de estas mismas imágenes sería
circular. Los perfiles grandes pesan más en las verosimilitudes.

Para usar Bayes desde el extractor, pasar el modelo mediante
`scraper.extract_info_json(id_images, data, bayes=bayes)`. Seleccionar únicamente
IDs del perfil que se quiere analizar; no mezclar perfiles para inferir un usuario.

Antes de interpretar probabilidades como estimaciones de uso real: validar con
perfiles externos separados por usuario, medir calibración (p. ej. Brier/log-loss)
y comprobar independencia, representatividad y el supuesto de un SO principal.
Fotos repetidas, compartidas, editadas, o cambios de teléfono pueden invalidarlos
y provocar exceso de confianza. Se deduplican referencias al mismo ID al filtrar
un perfil, pero no se detectan fotos idénticas con IDs distintos. La propiedad y
el teléfono actual no se pueden demostrar con estos metadatos. La probabilidad
es del SO principal bajo el modelo; los modelos concretos solo reciben puntuación.

## Inspección del JSON recibido

14 imágenes: 9 `Apple / iPhone 14`, 3 `Apple / iPhone`, 1 `Apple / iPhone 16 Pro Max`
y 1 sin marca/modelo con software `VSCO Android Version: v496 (3033494)`.
Resultado: 13 teléfonos iPhone, 0 cámaras reconocidas y 1 desconocido.
La mención de Android en el software no participa en la identificación.
El candidato con mayor
puntuación es `iphone 14`; eso no confirma que sea el teléfono actual.
No hay datos etiquetados suficientes para calibrar Bayes en este único JSON.

## Complejidad

Para n elementos de make y m de model (n=m por el contrato), se recorre cada par
una vez, se agregan conteos y conjuntos de días, y se recorren los candidatos
una vez para obtener el máximo. No hay producto cartesiano ni ordenamiento:
**O(n+m)** de tiempo esperado con tablas hash, y **O(n+m)** de memoria en el peor
caso. Bayes usa dos hipótesis y dos categorías: coste constante después del conteo.
La complejidad supone metadatos de longitud acotada; contando sus caracteres,
es O(n+m+L), donde L es su longitud total. La lectura JSON cuesta el tamaño del
archivo. El entrenamiento es lineal en el total de observaciones etiquetadas.

## Integración con el extractor

`Vsco.extract_info_json(id_images, json, *, bayes=None)` construye las listas
alineadas `make` y `model_phone_image` y llama una sola vez a
`identify_phone(make, model_phone_image, dates=dates, bayes=bayes)`.
Devuelve directamente el diccionario de identificación. Los valores ausentes
se conservan como `None` y los IDs repetidos se procesan una sola vez.

La función también se puede llamar directamente con solo las dos listas:

```python
from phone_ident import identify_phone

result = identify_phone(["Apple", "Samsung"], ["iPhone 14", "SM-S918B"])
print(result["phone"])
```

Desde `Documentos/vsco`, `python3 vsco.py` ejecuta el flujo del JSON local hasta
la identificación y muestra su resultado. `_testing__()` también devuelve ese
resultado. No abre el navegador; `req_profile()` sigue siendo la entrada para
descargar un perfil y devolver su JSON en memoria. No acepta una ruta de salida.
El módulo `phone_ident` se puede usar independientemente mediante sus funciones.

El flujo integrado sustituye la antigua entrada `python3 -m phone_ident`,
el adaptador `from_vsco_json` y los imports de `data.phone_ident`.
