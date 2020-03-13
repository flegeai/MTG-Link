#!/usr/bin/env python3
from __future__ import print_function
import os
import sys
import argparse
import csv
import re
import subprocess
from pathos.multiprocessing import ProcessingPool as Pool
#from multiprocessing import Pool
import gfapy
from gfapy.sequence import rc
from Bio import SeqIO, Align
from helpers import Gap, Scaffold, extract_barcodes, get_reads, mtg_fill, stats_align, get_position_for_edges, get_output_for_gfa, update_gfa_with_solution


#----------------------------------------------------
# Arg parser
#----------------------------------------------------
parser = argparse.ArgumentParser(prog="mtglink.py", usage="%(prog)s -in <GFA_file> -c <chunk_size> -bam <BAM_file> -reads <reads_file> -index <index_file> -f <freq_barcodes> [options]", \
                                description=("Gapfilling with linked read data, using MindTheGap in 'breakpoint' mode"))

parserMain = parser.add_argument_group("[Main options]")
parserMtg = parser.add_argument_group("[MindTheGap option]")

parserMain.add_argument('-in', dest="input", action="store", help="Input GFA file (format: xxx.gfa)", required=True)
parserMain.add_argument('-c', dest="chunk", action="store", type=int, help="Chunk size (bp)", required=True)
parserMain.add_argument('-bam', dest="bam", action="store", help="BAM file: linked reads mapped on current genome assembly (format: xxx.bam)", required=True)
parserMain.add_argument('-reads', dest="reads", action="store", help="File of indexed reads (format: xxx.fastq | xxx.fq)", required=True)
parserMain.add_argument('-index', dest="index", action="store", help="Prefix of barcodes index file (format: xxx.shelve)", required=True)
parserMain.add_argument('-f', dest="freq", action="store", type=int, default=2, help="Minimal frequence of barcodes extracted in the chunk of size '-c' [default: 2]")
parserMain.add_argument('-out', dest="outDir", action="store", default="./mtglink_results", help="Output directory [default './mtglink_results']")
parserMain.add_argument('-refDir', dest="refDir", action="store", help="Directory containing the reference sequences if any")
parserMain.add_argument('-contigs', dest="contigs", action="store", help="File containing the sequences of the contigs (format: xxx.fasta | xxx.fa)")
parserMain.add_argument('-line', dest="line", action="store", type=int, help="Line of GFA file input from which to start analysis (if not provided, start analysis from first line of GFA file input) [optional]")

parserMtg.add_argument('-bkpt', dest="breakpoint", action="store", help="Breakpoint file (with possibly offset of size k removed) (format: xxx.fasta | xxx.fa) [optional]")
parserMtg.add_argument('-k', dest="kmer", action="store", default=[51, 41, 31, 21],  nargs='*', type=int, help="k-mer size(s) used for gap-filling [default: [51, 41, 31, 21]]")
parserMtg.add_argument("--force", action="store_true", help="To force search on all '-k' values provided")
parserMtg.add_argument('-a', dest="abundance_threshold", action="store", default=[3, 2], nargs='*', type=int, help="Minimal abundance threshold for solid k-mers [default: [3, 2]]")
parserMtg.add_argument('-ext', dest="extension", action="store", type=int, help="Extension size of the gap on both sides (bp); determine start/end of gapfilling [default: '-k']")
parserMtg.add_argument('-max-nodes', dest="max_nodes", action="store", type=int, default=1000, help="Maximum number of nodes in contig graph [default: 1000]")
parserMtg.add_argument('-max-length', dest="max_length", action="store", type=int, default=10000, help="Maximum length of gapfilling (bp) [default: 10000]")
parserMtg.add_argument('-nb-cores', dest="nb_cores", action="store", type=int, default=4, help="Number of cores [default: 4]")
parserMtg.add_argument('-max-memory', dest="max_memory", action="store", type=int, default=8000, help="Max memory for graph building (in MBytes) [default: 8000]")
parserMtg.add_argument('-verbose', dest="verbosity", action="store", type=int, default=1, help="Verbosity level [default: 1]")

args = parser.parse_args()

if re.match('^.*.gfa$', args.input) is None:
    parser.error("The suffix of the GFA file should be: '.gfa'")

if re.match('^.*.bam$', args.bam) is None:
    parser.error("The suffix of the BAM file should be: '.bam'")

if args.contigs and re.match('^.*.fasta$', args.contigs) is None:
    parser.error("The suffix of the file containing the sequences of the contigs should be: '.fasta'")

if args.refDir is None and args.contigs is None:
    parser.error("Please provide either a directory containing the reference sequences or a file containing the sequences of the contigs")

#----------------------------------------------------
# Input files
#----------------------------------------------------
gfa_file = os.path.abspath(args.input)
if not os.path.exists(gfa_file):
    parser.error("The path of the GFA file doesn't exist")
gfa_name = gfa_file.split('/')[-1]
print("\nInput GFA file: " + gfa_file)

bam_file = os.path.abspath(args.bam)
if not os.path.exists(bam_file): 
    parser.error("The path of the BAM file doesn't exist")
print("BAM file: " + bam_file)

reads_file = os.path.abspath(args.reads)
if not os.path.exists(reads_file):
    parser.error("The path of the file of indexed reads doesn't exist")
print("File of indexed reads: " + reads_file)

index_file = os.path.abspath(args.index)
print("Barcodes index file (prefix): " + index_file)

if args.refDir is not None:
    refDir = os.path.abspath(args.refDir)
    if not os.path.exists(refDir):
        parser.error("The path of the directory containing the reference sequences doesn't exist")

if args.contigs is not None:
    scaffs_file = os.path.abspath(args.contigs)
    if not os.path.exists(scaffs_file):
        parser.error("The path of the file of contigs' sequences doesn't exist")
    print("File of contigs' sequences: " + scaffs_file)

#----------------------------------------------------
# Directories for saving results
#----------------------------------------------------
cwd = os.getcwd() 

#outDir
if not os.path.exists(args.outDir):
    os.mkdir(args.outDir)
try:
    os.chdir(args.outDir)
except:
    print("Something wrong with specified directory. Exception-", sys.exc_info())
    print("Restoring the path")
    os.chdir(cwd)
outDir = os.getcwd()
print("\nThe results are saved in " + outDir)

#mtgDir
mtgDir = outDir + "/mtg_results"
os.mkdir(mtgDir)

#statsDir
statsDir = outDir + "/alignments_stats"


#----------------------------------------------------
# gapfilling function - Pipeline
#----------------------------------------------------
def gapfilling(current_gap):

    #Open the input GFA file to get the corresponding Gap line
    gfa = gfapy.Gfa.from_file(gfa_file)
    for _gap_ in gfa.gaps:
        if str(_gap_) == current_gap:
            current_gap = _gap_
            gap = Gap(current_gap)

    gap.info()
    gap_label = gap.label()

    left_scaffold = Scaffold(current_gap, gap.left)
    right_scaffold = Scaffold(current_gap, gap.right)

    #If chunk size larger than length of scaffold(s), set the chunk size to the minimal scaffold length
    if args.chunk > left_scaffold.len or args.chunk > right_scaffold.len:
        args.chunk = min(left_scaffold.len, right_scaffold.len)

    #Save current G line into a temporary file
    tmp_gap_file = outDir +"/"+ str(gap_label) + "_tmp.gap"
    with open(tmp_gap_file, "w") as tmp_gap:
        tmp_gap.write(str(current_gap))
        tmp_gap.seek(0)

    #----------------------------------------------------
    # BamExtractor
    #----------------------------------------------------
    #Initiate a dictionary to count the occurences of each barcode
    barcodes_occ = {}
    
    #Obtain the left barcodes and store the elements in a set
    left_region = left_scaffold.chunk(args.chunk)
    left_barcodes_file = "{}{}.c{}.left.barcodes".format(left_scaffold.name, left_scaffold.orient, args.chunk)

    with open(left_barcodes_file, "w+") as left_barcodes:
        extract_barcodes(bam_file, gap_label, left_region, left_barcodes, barcodes_occ)
        left_barcodes.seek(0)
        #left = set(left_barcodes.read().splitlines())

    #Obtain the right barcodes and store the elements in a set
    right_region = right_scaffold.chunk(args.chunk)
    right_barcodes_file = "{}{}.c{}.right.barcodes".format(right_scaffold.name, right_scaffold.orient, args.chunk)

    with open(right_barcodes_file, "w+") as right_barcodes:
        extract_barcodes(bam_file, gap_label, right_region, right_barcodes, barcodes_occ)
        right_barcodes.seek(0)
        #right = set(right_barcodes.read().splitlines())

    #Calculate the union 
    union_barcodes_file = "{}.{}.g{}.c{}.bxu".format(gfa_name, str(gap_label), gap.length, args.chunk)
    with open(union_barcodes_file, "w") as union_barcodes:
        #union = left | right
        #filter barcodes by freq
        for (barcode, occurences) in barcodes_occ.items():
            if occurences >= args.freq:
                union_barcodes.write(barcode + "\n")

    #----------------------------------------------------
    # GetReads
    #----------------------------------------------------
    #Union: extract the reads associated with the barcodes
    union_reads_file = "{}.{}.g{}.c{}.rbxu.fastq".format(gfa_name, str(gap_label), gap.length, args.chunk)
    with open(union_reads_file, "w") as union_reads:
        get_reads(reads_file, index_file, gap_label, union_barcodes_file, union_reads)

    #----------------------------------------------------
    # Summary of union (barcodes and reads)
    #----------------------------------------------------
    bxu = sum(1 for line in open(union_barcodes_file, "r"))
    rbxu = sum(1 for line in open(union_reads_file, "r"))/4
    union_summary = [str(gap.id), str(gap.left), str(gap.right), gap.length, args.chunk, bxu, rbxu]

    #Remove the barcodes files
    subprocess.run(["rm", left_barcodes_file])
    subprocess.run(["rm", right_barcodes_file])
    subprocess.run(["rm", union_barcodes_file])

    #----------------------------------------------------
    # MindTheGap pipeline
    #----------------------------------------------------
    #Directory for saving the results from MindTheGap
    os.chdir(mtgDir)
        
    #Execute MindTheGap fill module on the union, in breakpoint mode
    for k in args.kmer:

        #MindTheGap output directory
        os.chdir(mtgDir)
    
        #----------------------------------------------------
        # Breakpoint file, with offset of size k removed
        #----------------------------------------------------
        #variable 'ext' is the size of the extension of the gap, on both sides [by default k]
        if args.extension is None:
            ext = k
        else:
            ext = args.extension

        bkpt_file = "{}.{}.g{}.c{}.k{}.offset_rm.bkpt.fasta".format(gfa_name, str(gap_label), gap.length, args.chunk, k)
        with open(bkpt_file, "w") as bkpt:
            line1 = ">bkpt1_GapID.{}_Gaplen.{} left_kmer.{}{}_len.{} offset_rm\n".format(str(gap_label), gap.length, left_scaffold.name, left_scaffold.orient, k)
            line2 = str(left_scaffold.sequence()[(left_scaffold.len - ext - k):(left_scaffold.len - ext)])
            line3 = "\n>bkpt1_GapID.{}_Gaplen.{} right_kmer.{}{}_len.{} offset_rm\n".format(str(gap_label), gap.length, right_scaffold.name, right_scaffold.orient, k)
            line4 = str(right_scaffold.sequence()[ext:(ext + k)])
            line5 = "\n>bkpt2_GapID.{}_Gaplen.{} left_kmer.{}{}_len.{} offset_rm\n".format(str(gap_label), gap.length, right_scaffold.name, gfapy.invert(right_scaffold.orient), k)
            line6 = str(rc(right_scaffold.sequence())[(right_scaffold.len - ext - k):(right_scaffold.len - ext)])
            line7 = "\n>bkpt2_GapID.{}_Gaplen.{} right_kmer.{}{}_len.{} offset_rm\n".format(str(gap_label), gap.length, left_scaffold.name, gfapy.invert(left_scaffold.orient), k)
            line8 = str(rc(left_scaffold.sequence())[ext:(ext + k)])
            bkpt.writelines([line1, line2, line3, line4, line5, line6, line7, line8])

        #----------------------------------------------------
        # Gapfilling
        #----------------------------------------------------
        for a in args.abundance_threshold:

            print("\nGapfilling of {}.{}.g{}.c{} for k={} and a={} (union)".format(gfa_name, str(gap_label), gap.length, args.chunk, k, a))
            input_file = os.path.join(outDir, union_reads_file)
            output = "{}.{}.g{}.c{}.k{}.a{}.bxu".format(gfa_name, str(gap_label), gap.length, args.chunk, k, a)
            max_nodes = args.max_nodes
            max_length = args.max_length
            if max_length == 10000 and gap.length >= 10000:
                max_length = gap.length + 1000
            nb_cores = args.nb_cores
            max_memory = args.max_memory
            verbose = args.verbosity
            mtg_fill(gap_label, input_file, bkpt_file, k, a, max_nodes, max_length, nb_cores, max_memory, verbose, output)

            if os.path.getsize(mtgDir +"/"+ output + ".insertions.fasta") > 0:
                insertion_file = os.path.abspath(mtgDir +"/"+ output + ".insertions.fasta")

                #Modify the 'insertion_file' and save it to a new file ('input_file') so that the 'solution x/y' part appears in record.id (and not just in record.description)
                input_file = os.path.abspath(mtgDir +"/"+ output + "..insertions.fasta")
                with open(insertion_file, "r") as original, open(input_file, "w") as corrected:
                    records = SeqIO.parse(original, "fasta")
                    for record in records:
                        if "solution" in record.description:
                            record.id = record.id + "_sol_" + record.description.split (" ")[-1]
                        else:
                            record.id = record.id + "_sol_1/1"
                        SeqIO.write(record, corrected, "fasta")

                #----------------------------------------------------
                # Stats of the alignments query_seq vs reference_seq
                #----------------------------------------------------
                #Get the reference sequence file
                if args.refDir is not None or args.contigs is not None:
                    print("\nStatistical analysis...")

                    if args.refDir is not None:
                        ref_file = refDir +"/"+ str(gap_label) +".g"+ str(gap.length) + ".ingap.fasta"
                    else:
                        ref_file = scaffs_file

                    if not os.path.isfile(ref_file):
                        print("Something wrong with the specified reference file. Exception-", sys.exc_info())

                    #Do statistics on the alignments of query_seq (found gapfill seq) vs reference_seq
                    else:
                        prefix = "{}.k{}.a{}".format(str(gap_label), k, a) 
                        stats_align(gap_label, input_file, ref_file, str(ext), prefix, statsDir)

                    #----------------------------------------------------
                    # Estimate quality of gapfilled sequence
                    #----------------------------------------------------
                    #Reader for alignment stats' files
                    ref_qry_file = statsDir + "/" + prefix + ".ref_qry.alignment.stats"
                    qry_qry_file = statsDir + "/" + prefix + ".qry_qry.alignment.stats"

                    if not os.path.exists(ref_qry_file):
                        parser.error("The 'xxx.ref_qry.alignment.stats' file doesn't exits")
                    elif not os.path.exists(qry_qry_file):
                        parser.error("The 'xxx.qry_qry.alignment.stats' file doesn't exits")

                    else:
                        ref_qry_output = open(ref_qry_file)
                        qry_qry_output = open(qry_qry_file)

                        reader_ref_stats = csv.DictReader(ref_qry_output, \
                                                        fieldnames=("Gap", "Len_gap", "Chunk", "k", "a", "Strand", "Solution", "Len_Q", "Ref", "Len_R", \
                                                                    "Start_ref", "End_ref", "Start_qry", "End_qry", "Len_alignR", "Len_alignQ", "%_Id", "%_CovR", "%_CovQ", "Frame_R", "Frame_Q", "Quality"), \
                                                        delimiter='\t')

                        reader_revcomp_stats = csv.DictReader(qry_qry_output, \
                                                            fieldnames=("Gap", "Len_gap", "Chunk", "k", "a", "Solution1", "Len_Q1", "Solution2", "Len_Q2", \
                                                                        "Start_Q1", "End_Q1", "Start_Q2", "End_Q2", "Len_align_Q1", "Len_align_Q2", "%_Id", "%_Cov_Q1", "%_Cov_Q2", "Frame_Q1", "Frame_Q2", "Quality"), \
                                                            delimiter='\t')
                        
                        #Obtain a quality score for each gapfilled seq
                        insertion_quality_file = os.path.abspath(mtgDir +"/"+ output + ".insertions_quality.fasta")
                        with open(input_file, "r") as query, open(insertion_quality_file, "w") as qualified:
                            for record in SeqIO.parse(query, "fasta"):

                                #----------------------------------------------------
                                #Ref = reference sequence of simulated gap
                                #----------------------------------------------------
                                if args.refDir is not None:
                                    #quality score for stats about the ref
                                    quality_ref = []
                                    for row in reader_ref_stats:
                                        if (row["Solution"] in record.id) and (("bkpt1" in record.id and row["Strand"] == "fwd") or ("bkpt2" in record.id and row["Strand"] == "rev")):
                                            quality_ref.append(row["Quality"])
                                    
                                    if quality_ref == []:
                                        quality_ref.append('D')

                                    ref_qry_output.seek(0)

                                    #quality score for stats about the reverse complement strand
                                    quality_revcomp = []
                                    for row in reader_revcomp_stats:
                                        if ((record.id.split('_')[-1] in row["Solution1"]) and (("bkpt1" in record.id and "fwd" in row["Solution1"]) or ("bkpt2" in record.id and "rev" in row["Solution1"]))) \
                                            or ((record.id.split('_')[-1] in row["Solution2"]) and (("bkpt1" in record.id and "fwd" in row["Solution2"]) or ("bkpt2" in record.id and "rev" in row["Solution2"]))):
                                            quality_revcomp.append(row["Quality"])
                                    if quality_revcomp == []:
                                        quality_revcomp.append('D')
                                    qry_qry_output.seek(0)

                                    #global quality score
                                    quality_gapfilled_seq = min(quality_ref) + min(quality_revcomp)
                                    
                                    record.description = "Quality " + str(quality_gapfilled_seq)
                                    SeqIO.write(record, qualified, "fasta")

                                    #If at least one good solution amongst all solution found, stop searching
                                    if re.match('^.*Quality [AB]{2}$', record.description):
                                        solution = True
                                    else:
                                        solution = False

                                #----------------------------------------------------
                                #Ref = contigs' sequences
                                #----------------------------------------------------
                                elif args.contigs is not None:
                                    #quality score for stats about the extension
                                    quality_ext_left = []
                                    quality_ext_right = []
                                    for row in reader_ref_stats:
                                        if (row["Solution"] in record.id) and (("bkpt1" in record.id and row["Strand"] == "fwd") or ("bkpt2" in record.id and row["Strand"] == "rev")) and (row["Ref"] == left_scaffold.name):
                                            quality_ext_left.append(row["Quality"])
                                        elif (row["Solution"] in record.id) and (("bkpt1" in record.id and row["Strand"] == "fwd") or ("bkpt2" in record.id and row["Strand"] == "rev")) and (row["Ref"] == right_scaffold.name):
                                            quality_ext_right.append(row["Quality"])
                                    if quality_ext_left == []:
                                        quality_ext_left.append('D')
                                    if quality_ext_right == []:
                                        quality_ext_right.append('D')

                                    ref_qry_output.seek(0)

                                    #quality score for stats about the reverse complement strand
                                    quality_revcomp = []
                                    for row in reader_revcomp_stats:
                                        if ((record.id.split('_')[-1] in row["Solution1"]) and (("bkpt1" in record.id and "fwd" in row["Solution1"]) or ("bkpt2" in record.id and "rev" in row["Solution1"]))) \
                                            or ((record.id.split('_')[-1] in row["Solution2"]) and (("bkpt1" in record.id and "fwd" in row["Solution2"]) or ("bkpt2" in record.id and "rev" in row["Solution2"]))):
                                            quality_revcomp.append(row["Quality"])
                                    if quality_revcomp == []:
                                        quality_revcomp.append('D')
                                    qry_qry_output.seek(0)

                                    #global quality score
                                    quality_gapfilled_seq = min(quality_ext_left) + min(quality_ext_right) + min(quality_revcomp)
                                    
                                    record.description = "Quality " + str(quality_gapfilled_seq)
                                    SeqIO.write(record, qualified, "fasta")

                                    #If at least one good solution amongst all solution found, stop searching
                                    if re.match('^.*Quality A[AB]{2}$', record.description) or re.match('^.*Quality BA[AB]$', record.description):
                                        solution = True
                                    else:
                                        solution = False

                            qualified.seek(0)

                        #remove the 'input_file' once done with it
                        subprocess.run(["rm", input_file])

                        #remplace the 'insertion_file' by the 'insertion_quality_file' (which is then renamed 'insertion_file')
                        subprocess.run(["rm", insertion_file])
                        subprocess.run(['mv', insertion_quality_file, insertion_file])


                output_for_gfa = []
                solutions = []
                #-------------------------------------------------------------------
                # GFA output: case gap, solution found (=query)
                #-------------------------------------------------------------------
                if solution == True:
                    with open(insertion_file, "r") as query:
                        for record in SeqIO.parse(query, "fasta"):  #x records loops (x = nb of query (e.g. nb of inserted seq))
                            solutions = []
                            seq = record.seq
                            strand = str(record.id).split('_')[0][-1]

                            #----------------------------------------------------
                            #Ref = reference sequence of simulated gap
                            #----------------------------------------------------
                            if args.refDir is not None:
                                #Update GFA with only the good solutions (the ones having a good quality score)
                                if (len(seq) > 2*ext) and (re.match('^.*Quality [AB]{2}$', record.description)):
                                    check = "True_" + str(strand)
                                    solutions.append(check)
                                    gfa_output = get_output_for_gfa(record, ext, k, gap.left, gap.right, left_scaffold, right_scaffold)
                                    output_for_gfa.append(gfa_output)
                                
                                else:
                                    check = "False_" + str(strand)
                                    solutions.append(check)

                            #----------------------------------------------------
                            #Ref = contigs' sequences
                            #----------------------------------------------------
                            elif args.contigs is not None:
                                #Update GFA with only the good solutions (the ones having a good quality score)
                                if (len(seq) > 2*ext) and (re.match('^.*Quality A[AB]{2}$', record.description) or re.match('^.*Quality BA[AB]$', record.description)):
                                    check = "True_" + str(strand)
                                    solutions.append(check)
                                    gfa_output = get_output_for_gfa(record, ext, k, gap.left, gap.right, left_scaffold, right_scaffold)
                                    output_for_gfa.append(gfa_output)

                                else:
                                    check = "False_" + str(strand)
                                    solutions.append(check)


                #Check if good solutions obtained for both fwd and rev strands
                if solution == True and ("True_1" and "True_2" in solutions): 
                    break

                else:
                    solution = False
                    os.chdir(mtgDir)
            

            #If no solution found, remove the 'xxx.insertions.fasta' and 'xxx.insertions.vcf' file
            else:
                output_for_gfa = []
                insertion_fasta = os.path.abspath(mtgDir +"/"+ output + ".insertions.fasta")
                insertion_vcf = os.path.abspath(mtgDir +"/"+ output + ".insertions.vcf")
                subprocess.run(["rm", insertion_fasta])
                subprocess.run(["rm", insertion_vcf])
                solution = False


        if solution == True and not args.force:
            break

        #----------------------------------------------------
        # GFA output: case gap, no solution
        #----------------------------------------------------
        elif k == min(args.kmer) and a == min(args.abundance_threshold):
            #Save the current G line into the variable 'output_for_gfa' only if this variable is empty 
            #(e.g. in the case where solution == False because we found only a good solution for one strand (and not for both strands), we update the output GFA file with this good solution, not with a gap line)
            if len(output_for_gfa) == 0:
                with open(tmp_gap_file, "r") as tmp_gap:
                    for line in tmp_gap.readlines():
                        output_for_gfa.append([line])


    #Remove the tmp.gap file
    os.chdir(outDir)
    subprocess.run(["rm", tmp_gap_file])


    return union_summary, output_for_gfa


#----------------------------------------------------
# Gapfilling with MindTheGap
#----------------------------------------------------
try:
    gfa = gfapy.Gfa.from_file(gfa_file)
    out_gfa_file = "mtglink_" + gfa_name

    #----------------------------------------------------
    # GFA output: case no gap
    #----------------------------------------------------
    #If no gap, rewrite all the lines into GFA output
    if len(gfa.gaps) == 0:
        with open(out_gfa_file, "w") as f:
            out_gfa = gfapy.Gfa()
            for line in gfa.lines:
                out_gfa.add_line(str(line))
            out_gfa.to_file(out_gfa_file)

    #----------------------------------------------------   
    # Fill the gaps
    #----------------------------------------------------
    #If gap, rewrite the H and S lines into GFA output
    if args.line is None:
        with open(out_gfa_file, "w") as f:
            out_gfa = gfapy.Gfa()
            out_gfa.add_line("H\tVN:Z:2.0")
            for line in gfa.segments:
                out_gfa.add_line(str(line))
            out_gfa.to_file(out_gfa_file)
        
    gaps = []
    gaps_label = []
    #If '-line' argument provided, start analysis from this line in GFA file input
    if args.line is not None:
        for _gap_ in gfa.gaps[(args.line - (len(gfa.segments)+2)):]:
            _gap_ = str(_gap_)
            gaps.append(_gap_)
    else:
        #Convert Gfapy gap line to a string to be able to use it with multiprocessing
        for _gap_ in gfa.gaps:
            _gap_ = str(_gap_)
            gaps.append(_gap_)

        p = Pool()

        with open("{}.union.sum".format(gfa_name), "w") as union_sum:
            legend = ["Gap ID", "Left scaffold", "Right scaffold", "Gap size", "Chunk size", "Nb barcodes", "Nb reads"]
            union_sum.write('\t'.join(j for j in legend))

            for union_summary, output_for_gfa in p.map(gapfilling, gaps):
                #Write all union_summary (obtained for each gap) from 'gapfilling' into the 'union_sum' file
                union_sum.write("\n" + '\t\t'.join(str(i) for i in union_summary))

                #Output the 'output_for_gfa' results (obtained for each gap) from 'gapfilling' in the output GFA file
                print("\nCreating the output GFA file...")
                if len(output_for_gfa[0]) > 1:          #solution found for the current gap
                    for output in output_for_gfa:
                        update_gfa_with_solution(outDir, gfa_name, output, out_gfa_file)
                else:                                   #no solution found for the current gap
                    out_gfa = gfapy.Gfa.from_file(out_gfa_file)
                    out_gfa.add_line(output_for_gfa[0][0])
                    out_gfa.to_file(out_gfa_file)

    #Remove the raw files obtained from MindTheGap
    os.chdir(mtgDir)
    # subprocess.run("rm -f *.h5", shell=True)
    subprocess.run("rm -f *.vcf", shell=True)


    #Give the output GFA file and the file containing the gapfill seq
    print("GFA file: " + out_gfa_file)


except Exception as e:
    print("\nException-")
    print(e)
    exc_type, exc_obj, exc_tb = sys.exc_info()
    fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
    print(exc_type, fname, exc_tb.tb_lineno)
    sys.exit(1)


print("\nSummary of the union: " +gfa_name+".union.sum")
print("The results from MindTheGap are saved in " + mtgDir)
print("The statistics from MTG-Link are saved in " + statsDir)
print("The GFA output file and the sequences file are saved in " + outDir)



#TODO: two modules, one when reference sequence provided (args.refDir), one when no reference sequence is provided (args.scaff)