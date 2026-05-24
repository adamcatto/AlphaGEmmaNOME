# DNA Sequence Foundation Model Agent Tasks

* Q+A of the form "what are the [functional / epigentic properties] of [gene / locus / fasta / dna string / other dna representation]?" in [organism] in [condition / cell type / tissue]?"
    * Examples:
        * "What are the epigenetic properties of BRCA1 in breast tissue?"
        * "What are the functional properties of chr17:43044295-43125483 in K562 cells?"
        * "What are the epigenetic properties of the following DNA sequence in liver tissue? [fasta string]"
    * Subtasks:
        * Resolve gene/locus/fasta to genomic coordinates (if needed)
        * Classify question into a set of "heads" (e.g. "chromatin accessibility", "transcription factor binding", "histone modifications", "gene expression", etc.)
        * For each head, run a forward pass of the appropriate model on the relevant window around the gene/locus/fasta
        * Rank tracks by relevance (e.g. mean signal in the window) and annotate with metadata (e.g. cell type, condition, etc.)
        * Generate a final answer summarizing the most relevant tracks and their properties
* Q+A of the form "what variants are in [gene / locus / fasta / dna string / other dna representation]?" in [organism] in [condition / cell type / tissue]?"
    * Examples:
        * "What variants are in BRCA1 in breast tissue?"
        * "What variants are in chr17:43044295-43125483 in K562 cells?"
        * "What variants are in the following DNA sequence in liver tissue? [fasta string]"
    * Subtasks:
        * Resolve gene/locus/fasta to genomic coordinates (if needed)
        * Query a variant database (e.g. dbSNP, ClinVar, gnomAD) for variants in the relevant window around the gene/locus/fasta
        * Filter variants by relevance (e.g. known pathogenic variants, variants with high allele frequency in the relevant population, etc.)
        * Annotate variants with metadata (e.g. clinical significance, allele frequency, etc.)
        * Generate a final answer summarizing the most relevant variants and their properties

* What TFs bind near [gene / locus / fasta / dna string / other dna representation] in [organism] in [condition / cell type / tissue]?"
    * Examples:
        * "What TFs bind near BRCA1 in breast tissue?"
        * "What TFs bind near chr17:43044295-43125483 in K562 cells?"
        * "What TFs bind near the following DNA sequence in liver tissue? [fasta string]"
    * Subtasks:
        * Resolve gene/locus/fasta to genomic coordinates (if needed)
        * Run a forward pass of a transcription factor binding model on the relevant window around the gene/locus/fasta
        * Rank TFs by relevance (e.g. mean signal in the window) and annotate with metadata (e.g. cell type, condition, etc.)
        * Generate a final answer summarizing the most relevant TFs and their properties

* Design a CRISPR guide RNA to target [gene / locus / fasta / dna string / other dna representation] in [organism] in [condition / cell type / tissue]?"
    * Examples:
        * "Design a CRISPR guide RNA to target BRCA1 in breast tissue?"
        * "Design a CRISPR guide RNA to target chr17:43044295-43125483 in K562 cells?"
        * "Design a CRISPR guide RNA to target the following DNA sequence in liver tissue? [fasta string]"
    * Subtasks:
        * Resolve gene/locus/fasta to genomic coordinates (if needed)
        * Run a CRISPR guide RNA design model on the relevant window around the gene/locus/fasta
        * Rank guide RNAs by relevance (e.g. predicted on-target efficacy, predicted off-target effects, etc.) and annotate with metadata (e.g. cell type, condition, etc.)
        * Generate a final answer summarizing the most relevant guide RNAs and their properties

* What are the predicted effects of [variant] in [gene / locus / fasta / dna string / other dna representation] in [organism] in [condition / cell type / tissue]?"
    * Examples:
        * "What are the predicted effects of the BRCA1 c.68_69del variant in breast tissue?"
        * "What are the predicted effects of the rs123456 variant in chr17:43044295-43125483 in K562 cells?"
        * "What are the predicted effects of the following variant in the context of the following DNA sequence in liver tissue? [variant description + fasta string]"
    * Subtasks:
        * Resolve gene/locus/fasta to genomic coordinates (if needed)
        * Query a variant effect prediction model (e.g. SIFT, PolyPhen, CADD) for the specified variant in the context of the relevant window around the gene/locus/fasta
        * Annotate predicted effects with metadata (e.g. predicted impact on protein function, predicted impact on splicing, on gene expression, on TF binding, etc.)
        * Generate a final answer summarizing the predicted effects of the variant and their properties

* What are the predicted effects of [CRISPR guide RNA] targeting [gene / locus / fasta / dna string / other dna representation] in [organism] in [condition / cell type / tissue]?"
    * Examples:
        * "What are the predicted effects of a CRISPR guide RNA targeting BRCA1 in breast tissue?"
        * "What are the predicted effects of a CRISPR guide RNA targeting chr17:43044295-43125483 in K562 cells?"
        * "What are the predicted effects of a CRISPR guide RNA targeting the following DNA sequence in liver tissue? [fasta string]"
    * Subtasks:
        * Resolve gene/locus/fasta to genomic coordinates (if needed)
        * Query a CRISPR effect prediction model (e.g. FORECasT, InDelphi) for the specified guide RNA in the context of the relevant window around the gene/locus/fasta
        * Annotate predicted effects with metadata (e.g. predicted indel spectrum, predicted impact on protein function, on splicing, on gene expression, on TF binding, etc.)
        * Generate a final answer summarizing the predicted effects of the guide RNA and their properties

* What are the known [regulatory elements] of [gene / locus / fasta / dna string / other dna representation] in [organism] in [condition / cell type / tissue]?"
    * Examples:
        * "What are the known regulatory elements of BRCA1 in breast tissue?"
        * "What are the known regulatory elements of chr17:43044295-43125483 in K562 cells?"
        * "What are the known regulatory elements of the following DNA sequence in liver tissue? [fasta string]"
    * Subtasks:
        * Resolve gene/locus/fasta to genomic coordinates (if needed)
        * Query a regulatory element database (e.g. ENCODE, Roadmap Epigenomics) for known regulatory elements in the relevant window around the gene/locus/fasta
        * Filter regulatory elements by relevance (e.g. active in the relevant cell type/tissue/condition, overlapping with relevant chromatin marks, etc.) and annotate with metadata (e.g. type of regulatory element, cell type, condition, etc.)
        * Generate a final answer summarizing the most relevant regulatory elements and their properties

* Design a synthetic regulatory element to achieve [desired regulatory effect] on [gene / locus / fasta / dna string / other dna representation] in [organism] in [condition / cell type / tissue]?"
    * Examples:
        * "Design a synthetic regulatory element to upregulate BRCA1 in breast tissue?"
        * "Design a synthetic regulatory element to downregulate chr17:43044295-43125483 in K562 cells?"
        * "Design a synthetic regulatory element to achieve a desired regulatory effect on the following DNA sequence in liver tissue? [fasta string]"
    * Subtasks:
        * Resolve gene/locus/fasta to genomic coordinates (if needed)
        * Query a synthetic regulatory element design model for the specified desired regulatory effect in the context of the relevant window around the gene/locus/fasta
        * Annotate designed regulatory elements with metadata (e.g. predicted strength of regulatory effect, predicted specificity for the target gene/locus/fasta, etc.)
        * Generate a final answer summarizing the designed regulatory elements and their properties

* What are the predicted 3D chromatin interactions of [gene / locus / fasta / dna string / other dna representation] in [organism] in [condition / cell type / tissue]?"
    * Examples:
        * "What are the predicted 3D chromatin interactions of BRCA1 in breast tissue?"
        * "What are the predicted 3D chromatin interactions of chr17:43044295-43125483 in K562 cells?"
        * "What are the predicted 3D chromatin interactions of the following DNA sequence in liver tissue? [fasta string]"
    * Subtasks:
        * Resolve gene/locus/fasta to genomic coordinates (if needed)
        * Query a 3D chromatin interaction prediction model (e.g. Akita, DeepC) for the specified gene/locus/fasta in the context of the relevant window around it
        * Annotate predicted interactions with metadata (e.g. interacting regions, predicted strength of interaction, etc.)
        * Generate a final answer summarizing the most relevant predicted 3D chromatin interactions and their properties

* Why is [gene / locus / fasta / dna string / other dna representation] expressed / accessible / modified in [organism] in [condition / cell type / tissue]?"
    * Examples:
        * "Why is BRCA1 expressed in breast tissue?"
        * "Why is chr17:43044295-43125483 accessible in K562 cells?"
        * "Why is the following DNA sequence modified in liver tissue? [fasta string]"
    * Subtasks:
        * Resolve gene/locus/fasta to genomic coordinates (if needed)
        * Query relevant models and databases to gather evidence about the regulatory landscape of the gene/locus/fasta in the relevant context (e.g. TF binding, chromatin accessibility, histone modifications, 3D interactions, etc.)
        * Synthesize gathered evidence to generate a coherent explanation for the observed expression/accessibility/modification pattern

* Query a custom model/database for [specific question] about [gene / locus / fasta / dna string / other dna representation] in [organism] in [condition / cell type / tissue]?"
    * Examples:
        * "Query a custom model/database for the evolutionary conservation of BRCA1 in breast tissue?"
        * "Query a custom model/database for the predicted impact of non-coding variants in chr17:43044295-43125483 in K562 cells?"
        * "Query a custom model/database for the presence of specific sequence motifs in the following DNA sequence in liver tissue? [fasta string]"
    * Subtasks:
        * Resolve gene/locus/fasta to genomic coordinates (if needed)
        * Identify and query the appropriate custom model/database for the specified specific question in the context of the relevant window around the gene/locus/fasta
        * Annotate retrieved information with metadata (e.g. source of information, relevance to the question, etc.)
        * Generate a final answer summarizing the retrieved information and its relevance to the specific question

* Query ENCODE/GTEx/other public dataset for [specific question] about [gene / locus / fasta / dna string / other dna representation] in [organism] in [condition / cell type / tissue]?"
    * Examples:
        * "Query ENCODE for the chromatin accessibility of BRCA1 in breast tissue?"
        * "Query GTEx for the expression of chr17:43044295-43125483 in K562 cells?"
        * "Query a public dataset for the presence of specific sequence motifs in the following DNA sequence in liver tissue? [fasta string]"
    * Subtasks:
        * Resolve gene/locus/fasta to genomic coordinates (if needed)
        * Identify and query the appropriate public dataset for the specified specific question in the context of the relevant window around the gene/locus/fasta
        * Annotate retrieved information with metadata (e.g. source of information, relevance to the question, etc.)
        * Generate a final answer summarizing the retrieved information and its relevance to the specific question

* Query a GWAS catalog for associations between variants in [gene / locus / fasta / dna string / other dna representation] and traits/diseases in [organism] in [condition / cell type / tissue]?"
    * Examples:
        * "Query a GWAS catalog for associations between variants in BRCA1 and traits/diseases in breast tissue?"
        * "Query a GWAS catalog for associations between variants in chr17:43044295-43125483 and traits/diseases in K562 cells?"
        * "Query a GWAS catalog for associations between variants in the following DNA sequence and traits/diseases in liver tissue? [fasta string]"
    * Subtasks:
        * Resolve gene/locus/fasta to genomic coordinates (if needed)
        * Query a GWAS catalog (e.g. NHGRI-EBI GWAS Catalog) for associations between variants in the relevant window around the gene/locus/fasta and traits/diseases
        * Filter associations by relevance (e.g. associations with high statistical significance, associations with traits/diseases relevant to the context, etc.) and annotate with metadata (e.g. associated trait/disease, p-value, etc.)
        * Generate a final answer summarizing the most relevant associations and their properties

* Query a gene expression database for co-expression patterns of [gene] with other genes in [organism] in [condition / cell type / tissue]?"
    * Examples:
        * "Query a gene expression database for co-expression patterns of BRCA1 with other genes in breast tissue?"
        * "Query a gene expression database for co-expression patterns of chr17:43044295-43125483 with other genes in K562 cells?"
        * "Query a gene expression database for co-expression patterns of the following DNA sequence with other genes in liver tissue? [fasta string]"
    * Subtasks:
        * Resolve gene/locus/fasta to genomic coordinates (if needed)
        * Query a gene expression database (e.g. GTEx, CCLE) for co-expression patterns of the specified gene/locus/fasta with other genes in the relevant context
        * Filter co-expression patterns by relevance (e.g. high correlation, relevance to the context, etc.) and annotate with metadata (e.g. correlated gene, correlation coefficient, etc.)
        * Generate a final answer summarizing the most relevant co-expression patterns and their properties

* Given a DNA sequence, determine whether it is a promoter/enhancer/silencer/other regulatory element and predict its properties (e.g. strength, specificity, etc.) in [organism] in [condition / cell type / tissue]?"
    * Examples:
        * "Given a DNA sequence, determine whether it is a promoter/enhancer/silencer/other regulatory element and predict its properties in breast tissue? [fasta string]"
        * "Given a DNA sequence, determine whether it is a promoter/enhancer/silencer/other regulatory element and predict its properties in K562 cells? [fasta string]"
        * "Given a DNA sequence, determine whether it is a promoter/enhancer/silencer/other regulatory element and predict its properties in liver tissue? [fasta string]"
    * Subtasks:
        * Run a regulatory element classification model on the provided DNA sequence to determine whether it is a promoter/enhancer/silencer/other regulatory element
        * If classified as a regulatory element, run additional models to predict its properties (e.g. strength, specificity, etc.) in the relevant context
        * Annotate predictions with metadata (e.g. predicted type of regulatory element, predicted strength of effect, predicted specificity for target genes/loci/fasta, etc.)
        * Generate a final answer summarizing the classification and predicted properties of the provided DNA sequence

* Given a variant, determine whether it is likely to be pathogenic/benign/other and predict its properties (e.g. impact on protein function, splicing, gene expression, TF binding, etc.) in [organism] in [condition / cell type / tissue]?"
    * Examples:
        * "Given a variant, determine whether it is likely to be pathogenic/benign/other and predict its properties in breast tissue? [variant description]"
        * "Given a variant, determine whether it is likely to be pathogenic/benign/other and predict its properties in K562 cells? [variant description]"
        * "Given a variant, determine whether it is likely to be pathogenic/benign/other and predict its properties in liver tissue? [variant description]"
    * Subtasks:
        * Run a variant effect prediction model on the provided variant to determine whether it is likely to be pathogenic/benign/other
        * Run additional models to predict the properties of the variant (e.g. impact on protein function, splicing, gene expression, TF binding, etc.) in the relevant context
        * Annotate predictions with metadata (e.g. predicted clinical significance, predicted impact on protein function, predicted impact on splicing, predicted impact on gene expression, predicted impact on TF binding, etc.)
        * Generate a final answer summarizing the predicted clinical significance and properties of the provided variant

* Given a CRISPR guide RNA, predict its on-target efficacy, off-target effects, and other relevant properties in [organism] in [condition / cell type / tissue]?"
    * Examples:
        * "Given a CRISPR guide RNA, predict its on-target efficacy, off-target effects, and other relevant properties in breast tissue? [guide RNA sequence]"
        * "Given a CRISPR guide RNA, predict its on-target efficacy, off-target effects, and other relevant properties in K562 cells? [guide RNA sequence]"
        * "Given a CRISPR guide RNA, predict its on-target efficacy, off-target effects, and other relevant properties in liver tissue? [guide RNA sequence]"
    * Subtasks:
        * Run a CRISPR guide RNA prediction model on the provided guide RNA sequence to predict its on-target efficacy and off-target effects in the relevant context
        * Annotate predictions with metadata (e.g. predicted on-target efficacy score, predicted off-target sites and their scores, etc.)
        * Generate a final answer summarizing the predicted properties of the provided CRISPR guide RNA

* What are the genomic coordinates of [gene / locus / fasta / dna string / other dna representation] in [organism]?"
    * Examples:
        * "What are the genomic coordinates of BRCA1 in human?"
        * "What are the genomic coordinates of chr17:43044295-43125483 in human?"
        * "What are the genomic coordinates of the following DNA sequence in human? [fasta string]"
    * Subtasks:
        * Resolve gene/locus/fasta to genomic coordinates using a reference genome and annotation database (e.g. Ensembl, RefSeq)
        * Annotate resolved coordinates with metadata (e.g. chromosome, start, end, strand, etc.)
        * Generate a final answer providing the genomic coordinates and their annotations

* Human genome annotation and reference data retrieval tasks:
    * Examples:
        * "Retrieve the reference genome sequence for chr17:43044295-43125483 in human?"
        * "Retrieve the gene annotation for BRCA1 in human?"
        * "Retrieve the known variants in BRCA1 in human?"
    * Subtasks:
        * Identify the appropriate reference genome and annotation databases for the specified organism
        * Query databases to retrieve the requested information (e.g. reference genome sequence, gene annotation, known variants, etc.)
        * Annotate retrieved information with metadata (e.g. source of information, relevance to the question, etc.)
        * Generate a final answer summarizing the retrieved information and its relevance to the specific question

* What minimal sequence edits would change the regulatory behavior of [gene / locus / sequence] in [context]?
    * Examples:
        * "What minimal edits would increase BRCA1 expression in breast tissue?"
        * "What mutations would reduce chromatin accessibility at chr17:43044295-43125483 in K562 cells?"
    * Subtasks:
        * Define objective (e.g. increase expression, decrease TF binding)
        * Run gradient-based or search-based sequence optimization
        * Evaluate candidate edits with forward passes
        * Rank edits by effect size and minimality (edit distance)
        * Generate a final answer summarizing actionable sequence changes

* What is the counterfactual effect of mutating [position(s)] in [sequence] in [context]?
    * Examples:
        * "What happens if we mutate the TATA box in this promoter?"
        * "What is the effect of mutating position 12345 A→G in this sequence?"
    * Subtasks:
        * Generate perturbed sequences
        * Run model inference on original vs perturbed
        * Compute delta across heads (expression, accessibility, TF binding)
        * Attribute effects to specific mechanisms
        * Generate a final answer summarizing causal effects

* What sequence features drive the predictions for [gene / locus / sequence] in [context]?
    * Examples:
        * "What motifs explain accessibility at BRCA1 in breast tissue?"
    * Subtasks:
        * Run attribution methods (e.g. integrated gradients, DeepLIFT)
        * Identify high-importance regions
        * Map to known motifs / regulatory grammar
        * Aggregate across tracks
        * Generate a final answer summarizing key drivers

* What transcription factor motifs are enriched in [sequence / region] in [context]?
    * Subtasks:
        * Scan sequence for motif instances
        * Weight motifs by model attribution or predicted binding
        * Compare against background
        * Report enriched motifs with effect sizes
        * Generate a final answer summarizing enriched motifs

* How do the regulatory properties of [gene / locus / sequence] differ between [context A] and [context B]?
    * Examples:
        * "How does BRCA1 regulation differ between breast and liver tissue?"
    * Subtasks:
        * Run model inference in both contexts
        * Compute differential signals across heads
        * Identify context-specific TFs, marks, accessibility
        * Generate a final answer summarizing key differences

* Which regions near [gene / locus] are differentially active between [conditions]?
    * Subtasks:
        * Slide window across region
        * Compute differential signal
        * Identify peaks of divergence
        * Annotate regulatory elements
        * Generate a final answer summarizing differential regions

* Integrate sequence-based predictions with experimental data for [gene / locus] in [context]
    * Examples:
        * "Compare predicted vs observed ATAC-seq at BRCA1 in K562 cells"
    * Subtasks:
        * Retrieve experimental tracks
        * Align with model predictions
        * Compute concordance metrics
        * Highlight discrepancies
        * Generate a final answer summarizing agreement/disagreement

* What regulatory mechanisms explain discrepancies between model predictions and observed data?
    * Subtasks:
        * Identify mismatched regions
        * Check missing modalities (e.g. 3D contacts, cofactors)
        * Hypothesize missing factors
        * Suggest experiments
        * Generate a final answer summarizing hypotheses

* Which distal elements regulate [gene] in [context]?
    * Subtasks:
        * Use 3D interaction predictions
        * Link enhancers to promoters
        * Rank distal elements by influence
        * Annotate with TF binding and chromatin marks
        * Generate a final answer summarizing distal regulators

* What is the regulatory landscape across a large genomic region (e.g. 1Mb) in [context]?
    * Subtasks:
        * Tile region into windows
        * Run model across tiles
        * Aggregate tracks into genome browser–like summary
        * Identify domains, peaks, boundaries
        * Generate a final answer summarizing landscape structure

* Generate a DNA sequence that achieves [target regulatory profile] in [context]
    * Examples:
        * "Generate a sequence with high accessibility and strong H3K27ac in liver cells"
    * Subtasks:
        * Define target vector across heads
        * Optimize sequence via gradient/search
        * Validate with forward passes
        * Generate a final answer with candidate sequences and predicted properties

* Generate variants of [sequence] that preserve function but increase robustness
    * Subtasks:
        * Define invariance constraints
        * Generate sequence variants
        * Evaluate variance in predictions
        * Select robust designs
        * Generate a final answer summarizing robust variants

* What are the predicted splicing patterns of [gene / sequence] in [context]?
    * Subtasks:
        * Run splice prediction heads
        * Identify splice sites, junction usage
        * Annotate isoforms
        * Generate a final answer summarizing splicing patterns

* How do variants affect splicing of [gene / sequence]?
    * Subtasks:
        * Introduce variant
        * Compare splice predictions
        * Quantify junction usage changes
        * Flag cryptic splice sites
        * Generate a final answer summarizing splicing effects

* How conserved is [sequence / region] across species?
    * Subtasks:
        * Query conservation tracks (phyloP, phastCons)
        * Align orthologous regions
        * Summarize conservation vs function
        * Generate a final answer summarizing conservation

* How do regulatory properties of [gene] differ across species?
    * Subtasks:
        * Map orthologous loci
        * Run models or retrieve data per species
        * Compare regulatory signals
        * Identify conserved vs divergent mechanisms
        * Generate a final answer summarizing differences

* How confident is the model in its predictions for [gene / locus / sequence] in [context]?
    * Subtasks:
        * Estimate uncertainty (ensembles, dropout)
        * Identify unstable regions
        * Report confidence intervals
        * Generate a final answer summarizing uncertainty

* Which predictions are most sensitive to input perturbations?
    * Subtasks:
        * Apply random perturbations
        * Measure variance in outputs
        * Identify fragile regulatory features
        * Generate a final answer summarizing sensitivity

* Automatically decompose a natural language genomics query into executable subtasks
    * Subtasks:
        * Parse query
        * Identify required tools/models
        * Build execution graph
        * Execute and aggregate results
        * Generate a final answer from composed outputs

* Cache, reuse, and compose intermediate model outputs across tasks
    * Subtasks:
        * Identify reusable computations (e.g. embeddings, predictions)
        * Store in structured format
        * Retrieve for downstream tasks
        * Reduce redundant computation
        * Generate a final answer leveraging cached results