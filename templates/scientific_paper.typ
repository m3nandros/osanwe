// Rosetta Scientific Paper Template for Typst
// Optimized for translated scientific articles

#let scientific_paper(
  title: none,
  authors: (),
  affiliations: (),
  abstract: none,
  keywords: (),
  date: none,
  lang: "ru",
  body,
) = {
  // Document metadata
  set document(
    title: title,
    author: authors.join(", "),
  )
  
  // Page setup
  set page(
    paper: "a4",
    margin: (
      top: 2.5cm,
      bottom: 2.5cm,
      left: 2.5cm,
      right: 2.5cm,
    ),
    header: context {
      if counter(page).get().first() > 1 {
        align(right, text(size: 9pt, fill: gray)[
          #title
        ])
      }
    },
    footer: context {
      align(center, text(size: 9pt)[
        #counter(page).display("1")
      ])
    },
  )
  
  // Text setup
  set text(
    font: ("New Computer Modern", "Noto Serif", "Times New Roman"),
    size: 11pt,
    lang: lang,
  )
  
  // Paragraph setup
  set par(
    justify: true,
    leading: 0.65em,
    first-line-indent: 1.25em,
  )
  
  // Heading setup
  set heading(numbering: "1.1")
  
  show heading.where(level: 1): it => {
    set text(size: 14pt, weight: "bold")
    set par(first-line-indent: 0em)
    v(1em)
    it
    v(0.5em)
  }
  
  show heading.where(level: 2): it => {
    set text(size: 12pt, weight: "bold")
    set par(first-line-indent: 0em)
    v(0.8em)
    it
    v(0.4em)
  }
  
  show heading.where(level: 3): it => {
    set text(size: 11pt, weight: "bold")
    set par(first-line-indent: 0em)
    v(0.6em)
    it
    v(0.3em)
  }
  
  // Figure and table captions
  show figure.where(kind: table): set figure(supplement: [Таблица])
  show figure.where(kind: image): set figure(supplement: [Рисунок])
  
  show figure.caption: it => {
    set text(size: 10pt)
    it
  }
  
  // Table styling
  show table: set table(
    stroke: 0.5pt + black,
    inset: 6pt,
  )
  
  show table.cell.where(y: 0): set text(weight: "bold")
  
  // Link styling
  show link: it => {
    set text(fill: rgb("#0066cc"))
    underline(it)
  }
  
  // Code blocks
  show raw.where(block: true): it => {
    set text(size: 9pt)
    block(
      width: 100%,
      fill: rgb("#f5f5f5"),
      inset: 8pt,
      radius: 3pt,
      it,
    )
  }
  
  // Inline code
  show raw.where(block: false): it => {
    box(
      fill: rgb("#f0f0f0"),
      inset: (x: 3pt, y: 0pt),
      radius: 2pt,
      it,
    )
  }
  
  // Math equations
  set math.equation(numbering: "(1)")
  
  // Blockquotes
  show quote: it => {
    block(
      width: 100%,
      inset: (left: 1em, right: 0.5em, y: 0.5em),
      stroke: (left: 2pt + gray),
      fill: rgb("#fafafa"),
      it,
    )
  }
  
  // === Title Page ===
  
  // Title
  align(center)[
    #v(2em)
    #text(size: 18pt, weight: "bold")[#title]
    #v(1em)
  ]
  
  // Authors
  if authors.len() > 0 {
    align(center)[
      #text(size: 12pt)[#authors.join(", ")]
      #v(0.5em)
    ]
  }
  
  // Affiliations
  if affiliations.len() > 0 {
    align(center)[
      #text(size: 10pt, fill: gray)[#affiliations.join("; ")]
      #v(0.5em)
    ]
  }
  
  // Date
  if date != none {
    align(center)[
      #text(size: 10pt)[#date]
      #v(1em)
    ]
  }
  
  // Abstract
  if abstract != none {
    v(1em)
    block(
      width: 100%,
      inset: 1em,
      stroke: 0.5pt + gray,
      radius: 3pt,
    )[
      #text(weight: "bold")[Аннотация]
      #v(0.5em)
      #text(size: 10pt)[#abstract]
    ]
    v(1em)
  }
  
  // Keywords
  if keywords.len() > 0 {
    text(weight: "bold", size: 10pt)[Ключевые слова: ]
    text(size: 10pt)[#keywords.join(", ")]
    v(1em)
  }
  
  // Horizontal rule before main content
  line(length: 100%, stroke: 0.5pt + gray)
  v(1em)
  
  // Main content
  body
}

// Helper function for creating simple tables
#let simple_table(headers, ..rows) = {
  let num_cols = headers.len()
  figure(
    table(
      columns: num_cols,
      align: (left,) + (center,) * (num_cols - 1),
      stroke: 0.5pt,
      table.header(..headers.map(h => [*#h*])),
      ..rows.pos().flatten(),
    ),
    kind: table,
  )
}

// Helper for citations (placeholder)
#let cite_ref(key) = {
  text(fill: rgb("#0066cc"))[\[#key\]]
}
