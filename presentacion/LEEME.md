# Plantilla Beamer · TEG

Compilar con XeLaTeX o LuaLaTeX (la plantilla usa fontspec):

    latexmk -xelatex presentacion.tex

Mantenga juntos `presentacion.tex`, `beamerthemeTEG.sty`, la carpeta `fonts/` (Schibsted Grotesk, licencia OFL) y la carpeta `logos/`.

Comandos del tema:
- `\tutor{}`, `\grado{}`, `\contacto{}`, `\etiquetaportada{}` para la portada y el cierre.
- `\logoizquierdo{}`, `\logoderecho{}` para los logos.
- `\destacado[Etiqueta]{Frase}` lámina a fondo violeta profundo.
- `\cierre` o `\cierre[Texto]` lámina final.
- `\cifra{valor}{descripción}` para resultados numéricos; `\tegetiqueta{}` para rótulos en mayúsculas; `\secundario{}` para texto gris.
- `\usetheme[sinprogreso]{TEG}` quita la barra de avance; `\sinpaginasdeseccion` desactiva las láminas de sección automáticas.
