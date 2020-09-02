#!/usr/bin/env python3
##############################################################################
##                                                                          ##
##          MATLAB Independent, Small & Safe, High Integrity Tools          ##
##                                                                          ##
##              Copyright (C) 2020, Florian Schanda                         ##
##                                                                          ##
##  This file is part of MISS_HIT.                                          ##
##                                                                          ##
##  MATLAB Independent, Small & Safe, High Integrity Tools (MISS_HIT) is    ##
##  free software: you can redistribute it and/or modify                    ##
##  it under the terms of the GNU Affero General Public License as          ##
##  published by the Free Software Foundation, either version 3 of the      ##
##  License, or (at your option) any later version.                         ##
##                                                                          ##
##  MISS_HIT is distributed in the hope that it will be useful,             ##
##  but WITHOUT ANY WARRANTY; without even the implied warranty of          ##
##  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the           ##
##  GNU Afferto General Public License for more details.                    ##
##                                                                          ##
##  You should have received a copy of the GNU Affero General Public        ##
##  License along with MISS_HIT. If not, see                                ##
##  <http://www.gnu.org/licenses/>.                                         ##
##                                                                          ##
##############################################################################

import json

from miss_hit_core import command_line
from miss_hit_core import work_package
from miss_hit_core import m_ast
from miss_hit_core.errors import Message_Handler, Error
from miss_hit_core.m_lexer import MATLAB_Lexer
from miss_hit_core.m_parser import MATLAB_Parser

from miss_hit import goto_ast


def make_type():
    # One of the key limitations of the MVP: we assume that everything
    # is a 32-bit signed integer.
    return goto_ast.SignedBV_Type(32)


def compile_name(mh, gst, stab, n_name):
    assert isinstance(mh, Message_Handler)
    assert isinstance(gst, goto_ast.GOTO_Symbol_Table)
    assert isinstance(stab, dict)
    assert isinstance(n_name, m_ast.Name)

    if isinstance(n_name, m_ast.Identifier):
        typ = make_type()
        pretty_name = str(n_name)
        mangled_name = stab[pretty_name]

        sym = goto_ast.Symbol_Expr(typ, mangled_name)
        return sym

    else:
        mh.error(n_name.loc(),
                 "mh_bmc does not %s names yet" %
                 n_name.__class__.__name__)


def compile_expression(mh, gst, stab, n_expr):
    assert isinstance(mh, Message_Handler)
    assert isinstance(gst, goto_ast.GOTO_Symbol_Table)
    assert isinstance(stab, dict)
    assert isinstance(n_expr, m_ast.Expression)

    if isinstance(n_expr, m_ast.Binary_Operation):
        typ = make_type()
        lhs = compile_expression(mh, gst, stab, n_expr.n_lhs)
        rhs = compile_expression(mh, gst, stab, n_expr.n_rhs)
        if n_expr.t_op.value == "+":
            sym = goto_ast.Plus_Expr(typ, [lhs, rhs])
            return sym
        else:
            mh.error(n_expr.t_op.location,
                     "mh_bmc does not the binary %s operation yet" %
                     n_expr.t_op.value)

    elif isinstance(n_expr, m_ast.Name):
        return compile_name(mh, gst, stab, n_expr)

    elif isinstance(n_expr, m_ast.Number_Literal):
        try:
            int_val = int(n_expr.t_value.value)
        except ValueError:
            mh.error(n_expr.t_value.location,
                     "mh_bmc only supports integer literals so far")
        typ = make_type()
        sym = goto_ast.Constant_Expr(typ, "%x" % int_val)
        return sym

    else:
        mh.error(n_expr.loc(),
                 "mh_bmc does not %s expressions yet" %
                 n_expr.__class__.__name__)


def compile_simple_assignment_statement(mh, gst, stab, n_stmt):
    assert isinstance(mh, Message_Handler)
    assert isinstance(gst, goto_ast.GOTO_Symbol_Table)
    assert isinstance(stab, dict)
    assert isinstance(n_stmt, m_ast.Simple_Assignment_Statement)

    typ = make_type()
    lhs = compile_name(mh, gst, stab, n_stmt.n_lhs)
    rhs = compile_expression(mh, gst, stab, n_stmt.n_rhs)

    sym_asn = goto_ast.Side_Effect_Expr_Assign(typ, lhs, rhs)
    sym_code = goto_ast.Code_Expression(sym_asn)
    return sym_code


def compile_statement(mh, gst, stab, n_stmt):
    assert isinstance(mh, Message_Handler)
    assert isinstance(gst, goto_ast.GOTO_Symbol_Table)
    assert isinstance(stab, dict)
    assert isinstance(n_stmt, m_ast.Statement)

    if isinstance(n_stmt, m_ast.Simple_Assignment_Statement):
        return compile_simple_assignment_statement(mh, gst, stab, n_stmt)

    else:
        mh.error(n_stmt.loc(),
                 "mh_bmc does not support %s yet" %
                 n_stmt.__class__.__name__)


def compile_sequence_of_statements(mh, gst, stab, n_seq):
    assert isinstance(mh, Message_Handler)
    assert isinstance(gst, goto_ast.GOTO_Symbol_Table)
    assert isinstance(stab, dict)
    assert isinstance(n_seq, m_ast.Sequence_Of_Statements)

    sym = goto_ast.Code_Block()

    for n_statement in n_seq.l_statements:
        sym.add_statement(compile_statement(mh, gst, stab, n_statement))

    return sym


def compile_function(mh, gst, n_fdef):
    assert isinstance(mh, Message_Handler)
    assert isinstance(gst, goto_ast.GOTO_Symbol_Table)
    assert isinstance(n_fdef, m_ast.Function_Definition)

    function_name = str(n_fdef.n_sig.n_name)

    sym_fn_type = goto_ast.Code_Type()
    stab = {}

    # Create names for the function inputs
    for n_param in n_fdef.n_sig.l_inputs:
        pretty_name = str(n_param)
        mangled_name = "::".join([function_name, "in", pretty_name])

        sym = goto_ast.Symbol(mangled_name, pretty_name)
        sym.is_parameter = True
        sym.value = goto_ast.Irep("nil")
        sym.typ = make_type()
        sym.location = n_param.loc()
        stab[pretty_name] = mangled_name
        gst.add_symbol(sym)

        sym_fn_type.add_parameter(goto_ast.Parameter(mangled_name, sym.typ))

    # Create names for the function outputs. Goto supports void
    # functions and functions with a single return. Right now we only
    # deal with non-void functions as that maps more or less 1:1.
    if len(n_fdef.n_sig.l_outputs) != 1:
        mh.error(n_fdef.loc(),
                 "mh_bmc supports only functions returning exactly one value")
    the_output = None
    for n_param in n_fdef.n_sig.l_outputs:
        pretty_name = str(n_param)
        mangled_name = "::".join([function_name, "out", pretty_name])

        sym = goto_ast.Symbol(mangled_name, pretty_name)
        sym.value = goto_ast.Irep("nil")
        sym.typ = make_type()
        sym.location = n_param.loc()
        stab[pretty_name] = mangled_name
        gst.add_symbol(sym)
        the_output = mangled_name

        sym_fn_type.set_return_type(sym.typ)

    # Complain about bits that we don't support yet
    for n_vld in n_fdef.l_validation:
        mh.error(n_vld.loc(), "not supported yet")
    for n_nst in n_fdef.l_nested:
        mh.error(n_nst.loc(), "nested function not supported yet")

    # Translate body
    sym = goto_ast.Symbol(function_name)
    sym.typ = sym_fn_type
    sym.value = compile_sequence_of_statements(mh, gst, stab, n_fdef.n_body)

    # Bolt on the function return
    sym_ret = goto_ast.Code_Return(goto_ast.Symbol_Expr(make_type(),
                                                        the_output))
    sym.value.add_statement(sym_ret)

    # Add to the symbol table
    gst.add_symbol(sym)


def compile_file(mh, n_tree):
    assert isinstance(mh, Message_Handler)
    assert isinstance(n_tree, m_ast.Node)

    if not isinstance(n_tree, m_ast.Function_File):
        mh.error(n_tree.loc(),
                 "mh_bmc only supports function files right now")

    gst = goto_ast.GOTO_Symbol_Table()

    # Compile all functions
    for n_fdef in n_tree.l_functions:
        compile_function(mh, gst, n_fdef)

    return gst


class MH_BMC_Result(work_package.Result):
    def __init__(self, wp):
        super().__init__(wp, True)


class MH_BMC(command_line.MISS_HIT_Back_End):
    def __init__(self, _):
        super().__init__("MH Bounded Model Checker")

    @classmethod
    def process_wp(cls, wp):
        # Create lexer
        lexer = MATLAB_Lexer(wp.mh,
                             wp.get_content(),
                             wp.filename,
                             wp.blockname)
        if wp.cfg.octave:
            lexer.set_octave_mode()
        if not wp.cfg.pragmas:
            lexer.process_pragmas = False
        if len(lexer.text.strip()) == 0:
            return MH_BMC_Result(wp)

        # Create parse tree and translate
        try:
            parser = MATLAB_Parser(wp.mh, lexer, wp.cfg)
            n_tree = parser.parse_file()
            gst = compile_file(wp.mh, n_tree)
        except Error:
            return MH_BMC_Result(wp)

        new_filename = wp.filename.replace(".m", ".json_symtab")
        with open(new_filename, "w") as fd:
            json.dump(gst.to_json(), fd, indent=2)
        wp.mh.info(n_tree.loc(),
                   "wrote goto symbol table to %s" % new_filename)

        return MH_BMC_Result(wp)


def main_handler():
    clp = command_line.create_basic_clp()
    options = command_line.parse_args(clp)

    mh = Message_Handler("bmc")
    mh.show_context = not options.brief
    mh.show_style   = False
    mh.show_checks  = False
    mh.autofix      = False

    bmc_backend = MH_BMC(options)
    command_line.execute(mh, options, {}, bmc_backend)


def main():
    command_line.ice_handler(main_handler)


if __name__ == "__main__":
    main()
