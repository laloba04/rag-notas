---
titulo: "ConfigMaps"
proyecto: Documentación de Kubernetes
url: https://github.com/kubernetes/website/blob/256a1f49419171f6c7fbdb1857146da1c63eb533/content/es/docs/concepts/configuration/configmap.md
licencia: CC BY 4.0
descargado: 2026-09-30
confidencialidad: publico
---

# ConfigMaps

Un configmap es un objeto de la API utilizado para almacenar datos no confidenciales en el formato clave-valor. Los Pods pueden utilizar los ConfigMaps como variables de entorno, argumentos de la linea de comandos o como ficheros de configuración en un Volumen.

Un ConfigMap te permite desacoplar la configuración de un entorno específico de una imagen de contenedor, así las aplicaciones son fácilmente portables.

> **Nota:** 
ConfigMap no proporciona encriptación.
Si los datos que quieres almacenar son confidenciales, utiliza un 
Secret en lugar de un ConfigMap,
o utiliza otras herramientas externas para mantener los datos seguros.

## Motivo

Utiliza un ConfigMap para crear una configuración separada del código de la aplicación.

Por ejemplo, imagina que estás desarrollando una aplicación que puedes correr en
tu propio equipo (para desarrollo) y en el cloud (para mantener tráfico real).
Escribes el código para configurar una variable llamada `DATABASE_HOST`.
En tu equipo configuras la variable con el valor `localhost`. 
En el cloud, la configuras con referencia a un kubernetes
Service  que expone el componente
de la base de datos en tu cluster.

Esto permite tener una imagen corriendo en un cloud y
tener el mismo código localmente para checkearlo si es necesario.

## Objeto ConfigMap

Un ConfigMap es un objeto de la API
que permite almacenar la configuración de otros objetos utilizados. Aunque muchos
objetos de kubernetes que tienen un `spec`, un ConfigMap tiene una sección `data` para
almacenar items, identificados por una clave, y sus valores.

El nombre del ConfigMap debe ser un 
nombre de subdominio DNS válido.

## ConfigMaps y Pods

Puedes escribir un Pod `spec` y referenciarlo a un ConfigMap y configurar el contenedor(es)
de ese Pod en base a los datos del ConfigMap. El Pod y el ConfigMap deben estar en
el mismo Namespace.

Este es un ejemplo de ConfigMap que tiene algunas claves con un valor simple,
y otras claves donde el valor tiene un formato de un fragmento de configuración.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: game-demo
data:
  # property-like keys; each key maps to a simple value
  player_initial_lives: "3"
  ui_properties_file_name: "user-interface.properties"
  #
  # file-like keys
  game.properties: |
    enemy.types=aliens,monsters
    player.maximum-lives=5
  user-interface.properties: |
    color.good=purple
    color.bad=yellow
    allow.textmode=true
```
Hay cuatro maneras diferentes de usar un ConfigMap para configurar
un contenedor dentro de un Pod:

1. Argumento en la linea de comandos como entrypoint de un contenedor
1. Variable de entorno de un contenedor
1. Como fichero en un volumen de solo lectura, para que lo lea la aplicación
1. Escribir el código para ejecutar dentro de un Pod que utiliza la API para leer el ConfigMap

Estos diferentes mecanismos permiten utilizar diferentes métodos para modelar
los datos que se van a usar.
Para los primeros tres mecanismos, el
kubelet utiliza la información
del ConfigMap cuando lanza un contenedor (o varios) en un Pod.

Para el cuarto método, tienes que escribir el código para leer el ConfigMap y sus datos.
Sin embargo, como estás utilizando la API de kubernetes directamente, la aplicación puede
suscribirse para obtener actualizaciones cuando el ConfigMap cambie, y reaccionar 
cuando esto ocurra. Accediendo directamente a la API de kubernetes, esta
técnica también permite acceder al ConfigMap en diferentes namespaces.

En el siguiente ejemplo el Pod utiliza los valores de `game-demo` para configurar el contenedor:
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: configmap-demo-pod
spec:
  containers:
    - name: demo
      image: game.example/demo-game
      env:
        # Define the environment variable
        - name: PLAYER_INITIAL_LIVES # Notice that the case is different here
                                     # from the key name in the ConfigMap.
          valueFrom:
            configMapKeyRef:
              name: game-demo           # The ConfigMap this value comes from.
              key: player_initial_lives # The key to fetch.
        - name: UI_PROPERTIES_FILE_NAME
          valueFrom:
            configMapKeyRef:
              name: game-demo
              key: ui_properties_file_name
      volumeMounts:
      - name: config
        mountPath: "/config"
        readOnly: true
  volumes:
    # You set volumes at the Pod level, then mount them into containers inside that Pod
    - name: config
      configMap:
        # Provide the name of the ConfigMap you want to mount.
        name: game-demo
        # An array of keys from the ConfigMap to create as files
        items:
        - key: "game.properties"
          path: "game.properties"
        - key: "user-interface.properties"
          path: "user-interface.properties"
```

Un ConfigMap no diferencia entre las propiedades de una linea individual y
un fichero con múltiples lineas y valores.
Lo importante es como los Pods y otros objetos consumen estos valores.

Para este ejemplo, definimos un Volumen y lo montamos dentro del contenedor
`demo` como `/config` creando dos ficheros,
`/config/game.properties` y `/config/user-interface.properties`,
aunque haya cuatro claves en el ConfigMap. Esto es debido a que enla definición
del Pod se especifica el array `items` en la sección `volumes`.
Si quieres omitir el array `items` entero, cada clave del ConfigMap se convierte en
un fichero con el mismo nombre que la clave, y tienes 4 ficheros.

## Usando ConfigMaps

Los ConfigMaps pueden montarse como volúmenes. También pueden ser utilizados por otras
partes del sistema, sin ser expuestos directamente al Pod. Por ejemplo,
los ConfigMaps pueden contener información para que otros elementos del sistema utilicen
para su configuración.

> **Nota:** 
La manera más común de usar los Configmaps es para configurar
los contenedores que están corriendo en un Pod en el mismo Namespace.
También se pueden usar por separado.

Por ejemplo,
quizá encuentres AddOns
u Operadores que
ajustan su comportamiento en base a un ConfigMap.

### Usando ConfigMaps como ficheros en un Pod

Para usar un ConfigMap en un volumen en un Pod:

1. Crear un ConfigMap o usar uno que exista. Múltiples Pods pueden utilizar el mismo ConfigMap.
1. Modifica la configuración del Pod para añadir el volumen en `.spec.volumes[]`. Pon cualquier nombre al Volumen, y tienes un campo `.spec.volumes[].configMap.name` configurado con referencia al objeto ConfigMap.
1. Añade un `.spec.containers[].volumeMounts[]` a cada contenedor que necesite el ConfigMap. Especifica `.spec.containers[].volumeMounts[].readOnly = true` y `.spec.containers[].volumeMounts[].mountPath` en un directorio sin uso donde quieras que aparezca el ConfigMap.
1. Modifica la imagen o el comando utilizado para que el programa busque los ficheros en el directorio. Cada clave del ConfigMap `data` se convierte en un un fichero en el `mountPath`.

En este ejemplo, el Pod monta un ConfigMap como un volumen:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: mypod
spec:
  containers:
  - name: mypod
    image: redis
    volumeMounts:
    - name: foo
      mountPath: "/etc/foo"
      readOnly: true
  volumes:
  - name: foo
    configMap:
      name: myconfigmap
```

Cada ConfigMap que quieras utilizar debe estar referenciado en `.spec.volumes`.

Si hay múltiples contenedores en el Pod, cada contenedor tiene su propio 
bloque `volumeMounts`, pero solo un `.spec.volumes` es necesario por cada ConfigMap.

#### ConfigMaps montados son actualizados automáticamente

Cuando un ConfigMap está siendo utilizado en un volumen y es actualizado, las claves son actualizadas también. 
El kubelet comprueba si el ConfigMap montado está actualizado cada periodo de sincronización.
Sin embargo, el kubelet utiliza su caché local para obtener el valor actual del ConfigMap.
El tipo de caché es configurable usando el campo `ConfigMapAndSecretChangeDetectionStrategy` en el
KubeletConfiguration struct.
Un ConfigMap puede ser propagado por vista (default), ttl-based, o simplemente redirigiendo
todas las consultas directamente a la API.
Como resultado, el retraso total desde el momento que el ConfigMap es actualizado hasta el momento
que las nuevas claves son proyectadas en el Pod puede ser tan largo como la sincronización del Pod
+ el retraso de propagación de la caché, donde la propagación de la caché depende del tipo de
caché elegido (es igual al retraso de propagación, ttl de la caché, o cero correspondientemente).

La característica alpha de kubernetes _Immutable Secrets and ConfigMaps_ provee una opción para configurar
Secrets individuales y ConfigMaps como inmutables. Para los Clústeres que usan ConfigMaps como extensión
(al menos decenas o cientos de un único ConfigMap montado en Pods), previene cambios en sus 
datos con las siguientes ventajas:

- protección de actualizaciones accidentales (o no deseadas) que pueden causar caídas de aplicaciones
- mejora el rendimiento del  Clúster significativamente reduciendo la carga del  kube-apiserver, 
cerrando las vistas para el ConfigMap marcado como inmutable.

Para usar esta característica, habilita el `ImmutableEmphemeralVolumes`
feature gate y configura
el campo del Secret o ConfigMap `immutable` como `true`. Por ejemplo:
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  ...
data:
  ...
immutable: true
```

> **Nota:** 
Una vez que un ConfigMap o un Secret es marcado como inmutable, _no_ es posible revertir el cambio
ni cambiar el contenido del campo `data`.  Solo se puede eliminar y recrear el ConfigMap.
Los Pods existentes mantiene un punto de montaje del ConfigMap eliminado - es recomendable
recrear los Pods.

## 

* Leer sobre Secrets.
* Leer Configure a Pod to Use a ConfigMap.
* Leer The Twelve-Factor App para entender el motivo de separar
  el código de la configuración.
