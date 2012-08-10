#!/usr/bin/python
# -*- coding: latin-1 -*-
"""JPyang: Java output plug-in
 * Copyright (c) 2012 Tail-F Systems AB, Stockholm, Sweden
 * All rights reserved.
 *
 * This software is the confidential and proprietary information of Tail-F
 * Systems AB. ('Confidential Information').  You shall not
 * disclose such Confidential Information and shall use it only in
 * accordance with the terms of the license agreement you entered into
 * with Tail-F Systems AB.
 *
 * To be released under GNU General Public License.
 * Please see http://www.opensource.org/licenses/gpl-3.0.html
 * or http://www.gnu.org/licenses/gpl.html

For complete functionality, invoke with:
> pyang \
    --path <yang search path> \
    --format java \
    --java-package <package.name> \
    --jpyang-verbose \
    --jpyang-ignore-errors \
    --jpyang-javadoc <javadoc directory path> \
    <file.yang>

Or, if you like to keep things simple:
> pyang -f jpyang -d <package.name> <file.yang>

"""

import optparse  # TODO: Deprecated in python 2.7, should use argparse instead
# ... See http://stackoverflow.com/questions/3217673/why-use-argparse-rather-than-optparse
# ... and http://docs.python.org/dev/library/argparse.html#upgrading-optparse-code
import os
import errno
import sys
import collections

from datetime import date
from numbers import Number

from pyang import plugin
from pyang import util
from pyang import error
from pyang import statements


# TODO: Might be more efficient to use dicts instead of set and list for these
java_reserved_words = {'abstract', 'assert', 'boolean', 'break', 'byte',
    'case', 'catch', 'char', 'class', 'const*', 'continue', 'default',
    'double', 'do', 'else', 'enum', 'extends', 'false',
    'final', 'finally', 'float', 'for', 'goto*', 'if',
    'implements', 'import', 'instanceof', 'int', 'interface', 'long',
    'native', 'new', 'null', 'package', 'private', 'protected',
    'public', 'return', 'short', 'static', 'strictfp', 'super',
    'switch', 'synchronized', 'this', 'throw', 'throws', 'transient',
    'true', 'try', 'void', 'volatile', 'while'}
"""A set of identifiers that are reserved in Java"""


java_lang = {'Boolean', 'Byte', 'Double', 'Float', 'Integer',
             'Long', 'Number', 'Object', 'Short', 'String'}
"""A subset of the java.lang classes"""


java_built_in = java_reserved_words | java_lang


immutable_stmts = {'type', 'typedef', 'namespace', 'prefix', 'organization',
    'contact', 'description', 'range'}
"""A set of statement keywords that should not have their arguments modified"""
# TODO: add more keywords to immutable_stmts


outputted_warnings = []
"""A list of warning message IDs that are used to avoid duplicate warnings"""


augmented_modules = {}
"""A dict of external modules that are augmented by this module"""


def pyang_plugin_init():
    """Registers an instance of the jpyang plugin"""
    plugin.register_plugin(JPyangPlugin())


class JPyangPlugin(plugin.PyangPlugin):
    """The plug-in class of JPyang"""

    def add_output_format(self, fmts):
        """Adds 'java' and 'jpyang' as valid output formats"""
        self.multiple_modules = False
        fmts['java'] = fmts['jpyang'] = self

    def add_opts(self, optparser):
        """Adds options to pyang"""
        optlist = [
            optparse.make_option(
                '-d', '--java-package',
                dest='directory',
                help='Generate output to DIRECTORY.'),
            optparse.make_option(
                '--jpyang-help',
                dest='jpyang_help',
                action='store_true',
                help='Print help on java format/JPyang usage and exit'),
            optparse.make_option(
                '--jpyang-debug',
                dest='debug',
                action='store_true',
                help='Print debug messages. Redundant if verbose mode is on.'),
            optparse.make_option(
                '--jpyang-javadoc',
                dest='javadoc_directory',
                help='Generate javadoc to JAVADOC_DIRECTORY.'),
            optparse.make_option(
                '--jpyang-no-schema',
                dest='no_schema',
                action='store_true',
                help='Do not generate schema.'),
            optparse.make_option(
                '--jpyang-verbose',
                dest='verbose',
                action='store_true',
                help='Verbose mode: Print detailed debug messages.'),
            optparse.make_option(
                '--jpyang-ignore-errors',
                dest='ignore',
                action='store_true',
                help='Ignore errors from validation.'),
            ]
        g = optparser.add_option_group('JPyang output specific options')
        g.add_options(optlist)
        self.o = optparser.parse_args()[0]

    def setup_ctx(self, ctx):
        """Called after ctx has been set up in main module. Checks if the
        jpyang help option was supplied and if not, that the -d or
        --java-package was used.

        ctx -- Context object as defined in __init__.py

        """
        if ctx.opts.jpyang_help:
            self.print_help()
            sys.exit(0)
        if ctx.opts.format in ['java', 'jpyang'] and not ctx.opts.directory:
            ctx.opts.directory = 'gen'
            print_warning(msg=('Option -d (or --java-package) not set, ' +
                'defaulting to "gen".'))

    def setup_fmt(self, ctx):
        """Disables implicit errors for the Context"""
        ctx.implicit_errors = False

    def emit(self, ctx, modules, fd):
        """Deletes any previous files in the supplied directory, creates
        directory structure and generates Java code to it.

        ctx     -- Context used to get output directory
        modules -- A list of pyang Statements, should be nothing else than
                   module and/or submodule statements.
        fd      -- File descriptor ignored.

        """
        if not ctx.opts.ignore:
            for (epos, etag, _) in ctx.errors:
                if (error.is_error(error.err_level(etag)) and
                    etag in ('MODULE_NOT_FOUND', 'MODULE_NOT_FOUND_REV')):
                    self.fatal("%s contains errors" % epos.top.arg)
                if (etag in ('TYPE_NOT_FOUND', 'FEATURE_NOT_FOUND',
                    'IDENTITY_NOT_FOUND', 'GROUPING_NOT_FOUND')):
                    print_warning(msg=(etag.lower() + ', generated class ' +
                        'hierarchy might be incomplete.'), key=etag)
                else:
                    print_warning(msg=(etag.lower() + ', aborting.'), key=etag)
                    self.fatal("%s contains errors" % epos.top.arg)
        directory = ctx.opts.directory
        d = directory.replace('.', os.sep)
        for module in modules:
            if module.keyword == 'module':
                # Generate Java classes
                module = make_valid_identifiers(module)
                src = ('module "' + module.arg + '", revision: "' +
                    util.get_latest_revision(module) + '".')
                generator = ClassGenerator(module, package=directory, src=src, ctx=ctx)
                generator.generate()
                for aug_module in augmented_modules.values():
                    src = ('module "' + aug_module.arg + '", revision: "' +
                        util.get_latest_revision(aug_module) + '".')
                    generator = ClassGenerator(aug_module, package=directory, src=src, ctx=ctx)
                    generator.generate()
                if ctx.opts.debug or ctx.opts.verbose:
                    print 'Java classes generation COMPLETE.'
                if not ctx.opts.no_schema:

                    # Generate external schema
                    schema_nodes = ['<schema>']
                    stmts = []
                    stmts.extend(module.substmts)
                    module_root = SchemaNode(module, '/')
                    schema_nodes.extend(module_root.as_list())
                    for aug_module in augmented_modules.values():
                        stmts.extend(aug_module.substmts)
                        aug_module_root = SchemaNode(aug_module, '/')
                        schema_nodes.extend(aug_module_root.as_list())
                    schema_generator = SchemaGenerator(stmts, '/', ctx)
                    schema_nodes.extend(schema_generator.schema_nodes())
                    for i in range(1, len(schema_nodes)):
                        # Indent all but the first and last line
                        if schema_nodes[i] in ('<node>', '</node>'):
                            schema_nodes[i] = ' ' * 4 + schema_nodes[i]
                        else:
                            schema_nodes[i] = ' ' * 8 + schema_nodes[i]
                    schema_nodes.append('</schema>')

                    name = capitalize_first(module.search_one('prefix').arg)
                    write_file(d, name + '.schema', '\n'.join(schema_nodes), ctx)
                    if ctx.opts.debug or ctx.opts.verbose:
                        print 'Schema generation COMPLETE.'

                augmented_modules.clear()

            else:
                print_warning(msg=('Ignoring schema tree rooted at "' +
                    module.keyword + ' ' + module.arg + '" - not a module'))

        # Generate javadoc
        for module in modules:
            if module.keyword == 'module':
                module = make_valid_identifiers(module)
                package_info_generator = PackageInfoGenerator(d, module, ctx)
                package_info_generator.generate_package_info()
        javadir = ctx.opts.javadoc_directory
        if javadir:
            if ctx.opts.debug or ctx.opts.verbose:
                print 'Generating javadoc...'
            if ctx.opts.verbose:
                os.system('javadoc -d ' + javadir + ' -subpackages ' + d)
            else:
                os.system(('javadoc -d ' + javadir + ' -subpackages ' + d +
                    ' >/dev/null'))
            if ctx.opts.debug or ctx.opts.verbose:
                print 'Javadoc generation COMPLETE.'
        if len(modules) != 1:
            print_warning(msg=('Generating code for several modules has not' +
                ' been tested thoroughly.'), ctx=ctx)

    def fatal(self, exitCode=1):
        """Raise an EmitError"""
        raise error.EmitError(self, exitCode)

    def print_help(self):
        """Prints a description of what JPyang is and how to use it"""
        print '''
The JPyang/Java output format can be used to generate a Java class hierarchy
from a single yang data model. Each module, container, list, etc. is
represented by a .java file which can be used to retrieve and/or edit
configurations (e.g. by calling methods to add, delete or replace statements).

One way to use the Java output format plug-in of pyang is to type
$ pyang -f java -d output.package.dir <file.yang>

The two formats java and jpyang produce identical results.

Type '$ pyang --help' for more details on how to use pyang.
'''


def print_warning(msg='', key='', ctx=None):
    """Prints msg to stderr if ctx is None or the debug or verbose flags are
    set in context ctx and key is empty or not in outputted_warnings. If key is
    not empty and not in outputted_warnings, it is added to it. If msg is empty
    'No support for type "' + key + '", defaulting to string.' is printed.

    """
    if ((not key or key not in outputted_warnings) and
        (not ctx or ctx.opts.debug or ctx.opts.verbose)):
        if msg:
            print >> sys.stderr, 'WARNING: ' + msg
            if key:
                outputted_warnings.append(key)
        else:
            print_warning(('No support for type "' + key + '", defaulting ' +
                'to string.'), key, ctx)


def write_file(d, file_name, file_content, ctx):
    """Creates the directory d if it does not yet exist and writes a file to it
    named file_name with file_content in it.

    """
    d = d.replace('.', os.sep)
    wd = os.getcwd()
    try:
        os.makedirs(d, 0777)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            pass  # The directory already exists
        else:
            raise
    try:
        os.chdir(d)
    except OSError as exc:
        if exc.errno == errno.ENOTDIR:
            print_warning(msg=('Unable to change directory to ' + d +
                '. Probably a non-directory file with same name as one of ' +
                'the subdirectories already exists.'), key=d, ctx=ctx)
        else:
            raise
    finally:
        if ctx.opts.verbose:
            print 'Writing file to: ' + os.getcwd() + os.sep + file_name
        os.chdir(wd)
    with open(d + os.sep + file_name, 'w+') as f:
        f.write(file_content)


def get_package(stmt, ctx):
    """Returns a string representing the package name of a java class generated
    from stmt, assuming that it has been or will be generated by JPyang.

    """
    sub_packages = collections.deque()
    while stmt.parent is not None:
        stmt = stmt.parent
        if stmt.parent is not None:
            sub_packages.appendleft(stmt.arg)
    full_package = collections.deque(ctx.opts.directory.split(os.sep))
    full_package.extend(sub_packages)
    if full_package and full_package[0] == 'src':
        full_package.popleft()
    return '.'.join(full_package)


def pairwise(iterable):
    """Returns an iterator that includes the next item also"""
    iterator = iter(iterable)
    item = iterator.next()  # throws StopIteration if empty.
    for next_item in iterator:
        yield (item, next_item)
        item = next_item
    yield (item, None)


def capitalize_first(string):
    """Returns string with its first character capitalized (if any)"""
    return string[:1].capitalize() + string[1:]


def camelize(string):
    """Removes hyphens and dots and replaces following character (if any) with
    its upper-case counterpart. Does not remove a trailing hyphen or dot.

    Returns an empty string if string argument is None.

    """
    camelized_str = ''
    if string is not None:
        iterator = pairwise(string)
        for character, next_character in iterator:
            if next_character and character in '-.':
                camelized_str += capitalize_first(next_character)
                iterator.next()
            else:
                camelized_str += character
    return camelized_str


def flatten(l):
    """Returns a flattened version of iterable l

    l must not have an attribute named values unless the return value values()
    is a valid substitution of l. Same applies to all items in l.

    Example: flatten([['12', '34'], ['56', ['7']]]) = ['12', '34', '56', '7']
    """
    res = []
    while hasattr(l, 'values'):
        l = l.values()
    for item in l:
        try:
            assert not isinstance(item, basestring)
            iter(item)
        except (AssertionError, TypeError):
            res.append(item)
        else:
            res.extend(flatten(item))
    return res


def make_valid_identifier(stmt):
    """Prepends a J character to the args field of stmt if it is a Java
    keyword. Replaces hyphens and dots with an underscore character.

    """
    if stmt and stmt.keyword not in immutable_stmts and stmt.arg:
        stmt.arg = camelize(stmt.arg)
        if stmt.arg in java_reserved_words:
            stmt.arg = 'J' + stmt.arg
    return stmt


def make_valid_identifiers(stmt):
    """Calls make_valid_identifier on stmt and its substatements"""
    stmt = make_valid_identifier(stmt)
    valid_substmts = []
    valid_i_children = []
    for sub in stmt.substmts:
        valid_substmts.append(make_valid_identifiers(sub))
    stmt.substmts = valid_substmts
    try:
        for ch in stmt.i_children:
            valid_i_children.append(make_valid_identifiers(ch))
        stmt.i_children = valid_i_children
    except AttributeError:
        pass
    return stmt


def get_types(yang_type, ctx):
    """Returns confm and primitive counterparts of yang_type, which is a type,
    typedef, leaf or leaf-list statement.

    """
    if yang_type.keyword in ('leaf', 'leaf-list'):
        yang_type = yang_type.search_one('type')
    assert yang_type.keyword in ('type', 'typedef'), 'argument is type, typedef or leaf'
    primitive = capitalize_first(camelize(yang_type.arg))
    netconf = 'com.tailf.jnc.Yang' + primitive
    if yang_type.arg in ('string', 'boolean'):
        pass
    elif yang_type.arg in ('enumeration', 'binary'):
        primitive = 'String'
    elif yang_type.arg in ('bits', ):
        primitive = 'BigInteger'
    elif yang_type.arg in ('instance-identifier', 'leafref', 'identityref'):
        primitive = 'Element'
    elif yang_type.arg in ('empty', ):
        primitive = 'boolean'
    elif yang_type.arg in ('int8', 'int16', 'int32', 'int64', 'uint8',
            'uint16', 'uint32', 'uint64'):
        integer_type = ['long', 'int', 'short', 'byte']
        if yang_type.arg[:1] == 'u':  # Unsigned
            integer_type.pop()
            integer_type.insert(0, 'long')
            netconf = 'com.tailf.jnc.YangUI' + yang_type.arg[2:]
        if yang_type.arg[-2:] == '64':
            primitive = integer_type[0]
        elif yang_type.arg[-2:] == '32':
            primitive = integer_type[1]
        elif yang_type.arg[-2:] == '16':
            primitive = integer_type[2]
        else:  # 8 bits
            primitive = integer_type[3]
    elif yang_type.arg == 'decimal64':
        primitive = 'BigDecimal'
    else:
        try:
            typedef = yang_type.i_typedef
        except AttributeError:
            type_id = get_package(yang_type, ctx) + yang_type.arg
            print_warning(key=type_id, ctx=ctx)
        else:
            if typedef is None and ctx.opts.verbose:
                print yang_type.keyword + ' ' + yang_type.arg
            basetype = get_base_type(typedef)
            package = get_package(typedef, ctx)
            typedef_arg = capitalize_first(camelize(yang_type.arg))
            return package + '.' + typedef_arg, get_types(basetype, ctx)[1]
    return netconf, primitive


def get_base_type(stmt):
    type_stmt = stmt.search_one('type')
    try:
        typedef = type_stmt.i_typedef
    except AttributeError:
        return type_stmt
    else:
        if typedef is not None:
            return get_base_type(typedef)
        else:
            return type_stmt


def extract_keys(stmt, ctx):
    """Returns the key statement of stmt and lists containing tuples with the
    confm (and primitive, respectively) type of the key and its identifier.

    stmt -- Typically a list statement
    ctx  -- Context used for passing debug flags

    """
    key = stmt.search_one('key')
    confm_keys = []
    primitive_keys = []
    only_strings = True
    for arg in key.arg.split(' '):
        key_type = stmt.search_one('leaf', arg).search_one('type')
        confm, primitive = get_types(key_type, ctx)
        confm_keys.append((confm, arg))
        primitive_keys.append((primitive, arg))
        only_strings *= primitive_keys[-1][0] == 'String'
        # XXX: 'b *= a' is syntactically equivalent to b = b and a

    return key, only_strings, confm_keys, primitive_keys


def extract_names(arg):
    """Returns a tuple with arg capitalized and prepended with .java, and arg
    capitalized.

    arg -- Any string, really

    """
    capitalized = capitalize_first(camelize(arg))
    return (capitalized + '.java', capitalized)


def get_date(date_format=0):
    """Returns a string representation of today's date. If date_format has the
    default value of 0, the format is dd/mm/yy. Otherwise, it is on the form
    yyyy-mm-dd.

    """
    time = date.today()
    if date_format == 0:
        return '/'.join(map(str, [time.day, time.month, time.year % 100]))
    else:
        return '-'.join(map(str, [time.year, time.month, time.day]))


def is_module(stmt):
    """Returns True iff stmt is a module or submodule."""
    return stmt.keyword == 'module' or stmt.keyword == 'submodule'


def is_container(stmt, strict=False):
    """Returns True iff stmt is a list, container or something of the sort."""
    return (stmt.keyword == 'container' or
        (not strict and stmt.keyword == 'list'))


def is_config(stmt):
    """Returns True if stmt is a configuration data statement"""
    config = None
    while config is None and stmt is not None:
        config = stmt.search_one('config')
        stmt = stmt.parent
    return config is None or config.arg == 'true'


def in_schema(stmt):
    """Returns True iff stmt is to be included in schema."""
    return (is_container(stmt) or
        stmt.keyword == 'module' or
        stmt.keyword == 'leaf')


def strip_first(s):
    """Returns s but with chars up to and including '.' or '/' removed"""
    return '.'.join(s.replace(os.sep, '.').split('.')[1:])


def indent(lines, level=1):
    """Returns a string consisting of all strings in lines concatenated,
    each string prepended by a level*4 number of spaces and appended with
    a newline, except the last which has no newline.

    lines -- list of strings
    level -- indentation level (number of spaces divided by 4)

    """
    # TODO: implement a more efficient version using replace on strings
    res = ''
    for line in lines:
        res += ' ' * level * 4 + line + '\n'
    return res[:-1]  # Don't include the last newline character


def java_docify(s):
    """Returns the string s, but with each row prepended by ' * '"""
    res = ''
    for row in s.splitlines():
        res += ' * ' + row + '\n'
    return res[:-1]  # Don't include the last newline character


class SchemaNode(object):

    def __init__(self, stmt, tagpath):
        self.stmt = stmt
        self.tagpath = tagpath

    def as_list(self):
        """Returns a string repr "node" element content for an XML schema"""
        res = ['<node>']
        stmt = self.stmt
        res.append('<tagpath>' + self.tagpath + '</tagpath>')
        if stmt.top is None:
            ns = stmt.search_one('namespace').arg
        else:
            ns = stmt.top.search_one('namespace').arg
        res.append('<namespace>' + ns + '</namespace>')
        res.append('<primitive_type>0</primitive_type>')

        min_occurs = '0'
        max_occurs = '-1'
        mandatory = stmt.search_one('mandatory')
        isMandatory = mandatory is not None and mandatory.arg == 'true'
        unique = stmt.search_one('unique')
        isUnique = unique is not None and unique.arg == 'true'
        key = None
        if stmt.parent is not None:
            key = stmt.parent.search_one('key')
        isKey = key is not None and key.arg == stmt.arg
        childOfContainerOrList = (stmt.parent is not None and is_container(stmt.parent))
        if (is_module(stmt) or isKey or
            (childOfContainerOrList and is_container(stmt, True))):
            min_occurs = '1'
            max_occurs = '1'
        if isMandatory:
            min_occurs = '1'
        if isUnique or childOfContainerOrList or is_container(stmt, True):
            max_occurs = '1'
        res.append('<min_occurs>' + min_occurs + '</min_occurs>')  # TODO: correct?
        res.append('<max_occurs>' + max_occurs + '</max_occurs>')  # TODO: correct?

        children = ''
        for ch in stmt.substmts:
            if ch.keyword in ('container', 'list', 'leaf', 'leaf-list'):
                children += ch.arg + ' '
        res.append('<children>' + children[:-1] + '</children>')

        res.append('<flags>0</flags>')
        res.append('<desc></desc>')
        res.append('</node>')
        return res


class SchemaGenerator(object):
    """Used to generate an external XML schema from a yang module"""

    def __init__(self, stmts, tagpath, ctx):
        self.stmts = stmts
        self.tagpath = tagpath
        self.ctx = ctx

    def schema_nodes(self):
        """Generate XML schema as a list of "node" elements"""
        res = []
        for stmt in self.stmts:
            if in_schema(stmt):
                node = SchemaNode(stmt, self.tagpath + stmt.arg + '/')
                substmt_generator = SchemaGenerator(stmt.substmts,
                    self.tagpath + stmt.arg + '/', self.ctx)
                if self.ctx.opts.verbose:
                    print 'Generating schema node "' + self.tagpath + '"...'
                res.extend(node.as_list())
                res.extend(substmt_generator.schema_nodes())
        return res


class YangType(object):
    """Provides an interface to maintain a list of defined yang types"""

    def __init__(self):
        self.defined_types = ['empty', 'int8', 'int16', 'int32', 'int64',
            'uint8', 'uint16', 'uint32', 'uint64', 'binary', 'bits', 'boolean',
            'decimal64', 'enumeration', 'identityref', 'instance-identifier',
            'leafref', 'string', 'union']
        """List of types represented by a confm.xs or generated class"""

    def defined(self, yang_type):
        """Returns true if yang_type is defined, else false"""
        return (yang_type in self.defined_types)

    def add(self, yang_type):
        """Gives yang_type "defined" status in this instance of YangType"""
        self.defined_types.append(yang_type)


class ClassGenerator(object):
    """Used to generate java classes from a yang module"""

    def __init__(self, stmt, package=None, src=None, ctx=None, path='', ns='',
            prefix_name='', top_level=False, yang_types=None, parent=None):
        """Constructor.

        stmt        -- A statement (sub)tree, parsed from a YANG model
        package     -- Name of Java package, also used as path to where files
                       should be written
        src         -- Filename of parsed yang module, or the module name and
                       revision if filename is unknown
        ctx         -- Context used to fetch option parameters
        path        -- The XPath of stmt in the original module
        ns          -- The XML namespace of the module
        prefix_name -- The module prefix
        top_level   -- Whether or not this is a top-level statement
        yang_types  -- An instance of the YangType class
        parent      -- ClassGenerator to copy arguments that were not supplied
                       from (if applicable)

        """  # TODO: Clarify why some arguments are optional
        self.stmt = stmt
        self.package = package
        self.src = src
        self.ctx = ctx
        self.path = path
        self.ns = ns
        self.prefix_name = prefix_name
        self.top_level = top_level
        self.yang_types = yang_types
        if yang_types is None:
            self.yang_types = YangType()
        if parent is not None:
            for attr in ['package', 'src', 'ctx', 'path', 'ns', 'prefix_name', 'yang_types']:
                if getattr(self, attr) is None:
                    setattr(self, attr, getattr(parent, attr))

    def generate(self):
        """Generates class(es) for self.stmt"""
        if self.stmt.keyword in ('module', 'submodule'):
            self.generate_classes()
        else:
            self.generate_class()

    def generate_classes(self):
        """Generates a Java class hierarchy from a module or submodule
        statement, allowing for netconf communication using the confm and inm
        libraries.

        """
        if self.stmt.keyword == 'module':
            ns_arg = self.stmt.search_one('namespace').arg
            prefix = self.stmt.search_one('prefix')
        elif self.stmt.keyword == 'submodule':
            parent_module = self.stmt.search_one('belongs-to')
            prefix = parent_module.search_one('prefix')
            ns_arg = '<unknown/prefix: ' + prefix.arg + '>'
        (self.filename, name) = extract_names(prefix.arg)

        for stmt in self.stmt.substmts:
            if stmt.keyword in ('container', 'list', 'augment', 'typedef'):
                child_generator = ClassGenerator(stmt, ns=ns_arg,
                    prefix_name=name, top_level=True, parent=self)
                child_generator.generate()

        if self.ctx.opts.verbose:
            print 'Generating Java class "' + self.filename + '"...'
        self.java_class = JavaClass(filename=self.filename,
                package=self.package, description=('The root class for namespace ' +
                    ns_arg + ' (accessible from \n * ' + name +
                    '.NAMESPACE) with prefix "' + prefix.arg + '" (' + name +
                    '.PREFIX).'),
                source=self.src)
        self.java_class.add_field(static_string('NAMESPACE', ns_arg))
        self.java_class.add_field(static_string('PREFIX', prefix.arg))
        self.java_class.add_enabler(enable(name))
        self.java_class.add_schema_registrator(register_schema(name))
        self.write_to_file()

    def generate_class(self):
        """Generates a Java class hierarchy providing an interface to a YANG
        module. Uses mutual recursion with generate_child.

        """
        stmt = self.stmt
        self.filename, _ = extract_names(stmt.arg)
        fields = []

        self.java_class = JavaClass(filename=self.filename,
                package=self.package,
                description='This class represents a "' + self.path +
                    stmt.arg + '" element\n * from the namespace ' + self.ns,
                source=self.src,
                superclass='YangElement')

        i_children_exists = (hasattr(stmt, 'i_children')
            and stmt.i_children is not None
            and stmt.i_children != [])

        # If augment, add target module to augmented_modules dict
        if stmt.keyword == 'augment':
            if not hasattr(stmt, "i_target_node"):  # TODO: EAFP
                warn_msg = 'Target missing from augment statement'
                print_warning(warn_msg, warn_msg, self.ctx)
            else:
                target = stmt.i_target_node
                augmented_modules[target.top.arg] = target.top
            return  # XXX: Do not generate a class for the augment statement

        # TODO: preserve correct order in generated class
        expanded_i_children = []
        if i_children_exists:
            for ch in stmt.i_children:
                fields.extend(self.generate_child(ch))

            def expand(children):
                res = []
                res.extend(children)
                for ch in children:
                    sub_children = []
                    try:
                        sub_children.extend(ch.i_children)
                    except AttributeError:
                        pass
                    sub_children.extend(ch.substmts)
                    res.extend(expand(sub_children))
                return res
            expanded_i_children = expand(stmt.i_children)

        # TODO: Avoid quadratic time duplication check (maybe use a set)
        for sub in stmt.substmts:
            if sub not in expanded_i_children:
                fields.extend(self.generate_child(sub))

        if self.ctx.opts.verbose:
            print 'Generating Java class "' + self.filename + '"...'

        gen = MethodGenerator(stmt, self.ctx)

        for constructor in gen.constructors():
            self.java_class.add_constructor(constructor)

        for cloner in gen.cloners():
            self.java_class.add_cloner(cloner)

        try:
            for i, method in enumerate(gen.setters()):
                self.java_class.append_access_method(str(i), method)
        except TypeError:
            pass  # setters not implemented

        checker = gen.checker()
        if checker is not None:
            self.java_class.append_access_method('check', checker)

        support_method = gen.support_method(fields)
        if support_method is not None:
            self.java_class.add_support_method(support_method)

        if stmt.keyword != 'typedef':  # TODO: Only add key name getter when relevant
            self.java_class.add_name_getter(key_names(stmt))
            self.java_class.add_name_getter(children_names(stmt))
        elif stmt.keyword == 'typedef':
            type_stmt = stmt.search_one('type')
            super_type = get_types(type_stmt, self.ctx)[0]
            self.java_class.superclass = super_type

            # If supertype is derived, make sure a class for it is generated
            if type_stmt.i_typedef:
                if not self.yang_types.defined(type_stmt.i_typedef.arg):
                    typedef_generator = ClassGenerator(type_stmt.i_typedef,
                        package='src.' + get_package(type_stmt.i_typedef, self.ctx),
                        path=self.package.replace('.', os.sep) + os.sep, ns=None,
                        prefix_name=None, parent=self)
                    typedef_generator.generate()
                    self.yang_types.add(type_stmt.i_typedef.arg)
            self.yang_types.add(stmt.arg)

        self.write_to_file()

    def generate_child(self, sub):
        """Appends access methods to class for children in the YANG module.
        Returns a list which contains the name of sub if it is a container,
        otherwise an empty list is returned. Uses mutual recursion with
        generate_class.

        For this function to work, self.java_class must be defined.

        sub -- A data model subtree statement. Its parent most not be None.

        """
        fields = []
        add = self.java_class.append_access_method  # XXX: add is a function
        if sub.keyword in ('list', 'container', 'typedef'):
            child_generator = ClassGenerator(stmt=sub,
                package=self.package + '.' + sub.parent.arg,
                path=self.path + sub.parent.arg + os.sep, ns=None,
                prefix_name=None, parent=self)
            child_generator.generate()
#            name = extract_names(sub.arg)[1]
#            sub_package = get_package(sub, self.ctx) + '.' + name
#            self.java_class.add_import(sub_package, sub_package)
            if sub.keyword == 'list':
                child_gen = MethodGenerator(sub, self.ctx)
                for access_method in child_gen.gen.parent_access_methods():
                    add(sub.arg, access_method)
            elif sub.keyword == 'container':
                fields.append(sub.arg)
                self.java_class.add_field(child_field(sub))
                child_gen = MethodGenerator(sub, self.ctx)
                for access_method in child_gen.gen.parent_access_methods():
                    add(sub.arg, access_method)
        elif sub.keyword in ('leaf', 'leaf-list'):
            type_stmt = sub.search_one('type')
            type_str1, type_str2 = get_types(type_stmt, self.ctx)
            if sub.keyword == 'leaf':
                key = sub.parent.search_one('key')
                if key is not None and sub.arg in key.arg.split(' '):
                    temp = statements.Statement(None, None, None, 'key',
                        arg=sub.arg)  # TODO: Copy sub instead?
                    optional = False

                    # Pass temp to avoid multiple keys
                    add(sub.arg, access_methods_comment(temp, optional))
                else:
                    # TODO: ensure that the leaf is truly optional
                    optional = True
                    add(sub.arg, access_methods_comment(sub, optional))
                add(sub.arg, get_value(sub, ret_type=type_str1))
                add(sub.arg, set_leaf_value(sub, prefix=self.prefix_name, arg_type=type_str1))
                add(sub.arg, set_leaf_value(sub, prefix='', arg_type='String',
                        confm_type=type_str1))
                if type_str2 != 'String':
                    add(sub.arg, set_leaf_value(sub, prefix='',
                        arg_type=type_str2, confm_type=type_str1))
                if optional:
                    add(sub.arg, unset_value(sub))
                add(sub.arg, add_value(sub, self.prefix_name))
                if optional:
                    add(sub.arg, mark(sub, 'replace'))
                    add(sub.arg, mark(sub, 'merge'))
                    add(sub.arg, mark(sub, 'create'))
                    add(sub.arg, mark(sub, 'delete'))
            elif sub.keyword == 'leaf-list':
                add(sub.arg, access_methods_comment(sub, optional=False))
                add(sub.arg, child_iterator(sub))
                add(sub.arg, set_leaf_value(sub, prefix=self.prefix_name, arg_type=type_str1))
                add(sub.arg, set_leaf_value(sub, prefix='', arg_type='String',
                        confm_type=type_str1))
                if type_str2 != 'String':
                    add(sub.arg, set_leaf_value(sub, prefix='',
                        arg_type=type_str2, confm_type=type_str1))
                add(sub.arg, delete_stmt(sub,
                        args=[(type_str1, sub.arg + 'Value')], keys=False))
                add(sub.arg, delete_stmt(sub, args=[(type_str1, sub.arg + 'Value')],
                        string=True, keys=False))
                add(sub.arg, add_value(sub, self.prefix_name))
                add(sub.arg, mark(sub, 'replace', arg_type=type_str1))
                add(sub.arg, mark(sub, 'replace', arg_type='String'))
                add(sub.arg, mark(sub, 'merge', arg_type=type_str1))
                add(sub.arg, mark(sub, 'merge', arg_type='String'))
                add(sub.arg, mark(sub, 'create', arg_type=type_str1))
                add(sub.arg, mark(sub, 'create', arg_type='String'))
                add(sub.arg, mark(sub, 'delete', arg_type=type_str1))
                add(sub.arg, mark(sub, 'delete', arg_type='String'))
        return fields

    def write_to_file(self):
        write_file(self.package,
                   self.filename,
                   self.java_class.java_class(),
                   self.ctx)


class PackageInfoGenerator(object):
    """Used to generate package-info.java files, with meaningful content"""

    def __init__(self, directory, stmt, ctx):
        """Initializes a generator with package directory path, top level
        statement and context for options.

        stmt      -- Statement representing any YANG module subtree
        directory -- The package directory as a string
        ctx       -- Context for options

        """
        self.d = directory
        self.stmt = stmt
        self.ctx = ctx

    def generate_package_info(self):
        """Main generator method: generates package-info content and writes it
        to a file

        """
        is_java_file = lambda s: s.endswith('.java')
        is_not_java_file = lambda s: not is_java_file(s)
        directory_listing = os.listdir(self.d)
        java_files = filter(is_java_file, directory_listing)
        dirs = filter(is_not_java_file, directory_listing)
        class_hierarchy = self.generate_javadoc(self.stmt.substmts, java_files)
        write_file(self.d, 'package-info.java', self.gen_package_info(class_hierarchy,
            self.d.replace(os.sep, '.')), self.ctx)
        for directory in dirs:
            for sub in self.stmt.substmts:
                # XXX: refactor
                if(camelize(capitalize_first(sub.arg)) ==
                   camelize(capitalize_first(directory).replace('.',
                        '?')).replace('?', '.')):
                    old_d = self.d
                    self.d += os.sep + directory
                    old_stmt = self.stmt
                    self.stmt = sub
                    self.generate_package_info()
                    self.d = old_d
                    self.stmt = old_stmt

    @staticmethod
    def generate_javadoc(stmts, java_files):
        """Generates a list of class filenames and lists of their subclasses'
        filenames, positioned immediately after each filename if any.

        stmts      -- list of statements representing a YANG module tree node
        java_files -- list of Java class filenames that has been generated

        """
        hierarchy = []
        for stmt in stmts:
            filename = extract_names(stmt.arg)[0]
            if filename in java_files:
                java_files.remove(filename)
                hierarchy.append(filename)
                children = PackageInfoGenerator.generate_javadoc(stmt.substmts, java_files)
                if children:
                    hierarchy.append(children)
        return hierarchy

    @staticmethod
    def parse_hierarchy(hierarchy):
        """Returns html for a list of javadoc pages corresponding to the .java
        files in the hierarchy list.

        hierarchy -- a tree of .java files represented as a list, for example:
            ['Foo.java', ['Bar.java', ['Baz.java'], 'Qu.java']] would represent the
            hierarchy structure:
            Foo
            |   Bar
            |   |   Baz
            |   Qu

            That is, Baz is a child of Bar in the data model tree, and Bar and Qu
            are children of the top level element Foo.

        """
        res = ''
        for entry in hierarchy:
            if PackageInfoGenerator.is_not_list(entry):
                body = '    <a href="' + entry[:-5] + '.html">' + entry[:-5] + '</a>'
                res += PackageInfoGenerator.html_list(body, 1, tag='li')
            else:
                body = PackageInfoGenerator.parse_hierarchy(entry)
                res += PackageInfoGenerator.html_list(body, 1)
            if body[-1:] != '\n':
                res += '\n'
        return res

    @staticmethod
    def is_not_list(entry):
        """Returns False iff entry is instance of list"""
        return not isinstance(entry, list)

    @staticmethod
    def html_list(body, indent_level, tag='ul'):
        """Returns a string representing javadoc with a <ul> html-element if ul,
        else with a <li> html-element.

        """
        body = '<' + tag + '>\n' + body
        if body[-1:] != '\n':
            body += '\n'
        body += '</' + tag + '>'
        return indent(body.split('\n'), indent_level)

    def gen_package_info(self, class_hierarchy, package):
        """Writes a package-info.java file to the package directory with a high
        level description of the package functionality and requirements.

        class_hierarchy -- A tree represented as a list as in parse_hierarchy
        ctx             -- Context used only for debugging purposes

        """
        if self.ctx.opts.verbose:
            print 'Generating package description package-info.java...'
        src = source = ''
        decapitalize = lambda s: s[:1].lower() + s[1:] if s else ''
        top_level_entries = filter(self.is_not_list, class_hierarchy)
        for entry in top_level_entries:
            module_arg = decapitalize(entry[:-5])
            rev = 'unknown'  # FIXME: Fetch revision
            src += 'module "' + module_arg + '" (rev "' + rev + '"), '
        if len(top_level_entries) > 1:
            source += 's'
        source += '\n' + src[:-2]
        html_hierarchy = self.html_list(self.parse_hierarchy(class_hierarchy), 0)
        specification = ('''
    This class hierarchy was generated from the Yang module''' + source +
    ' by the <a target="_top" href="https://github.com/Emil-Tail-f/JPyang">' +
    'JPyang</a> plugin of <a target="_top" ' +
    '''href="http://code.google.com/p/pyang/">pyang</a>.
    The generated classes may be used to manipulate pieces of configuration data
    with NETCONF operations such as edit-config, delete-config and lock. These
    operations are typically accessed through the ConfM Java library by
    instantiating Device objects and setting up NETCONF sessions with real devices
    using a compatible YANG model.

    ''')
        # XXX: These strings should probably be rewritten for code readability and
        # ... to comply with the actual functionality of the class
        return ('/**' + java_docify(specification + html_hierarchy) + ('''
     *
     * @see <a target="_top" href="https://github.com/Emil-Tail-f/JPyang">''' +
        'JPyang project page</a>\n * @see <a target="_top" ' +
        'href="ftp://ftp.rfc-editor.org/in-notes/rfc6020.txt">' +
        'RFC 6020: YANG - A Data Modeling Language for the Network ' +
        'Configuration Protocol (NETCONF)</a>\n * @see <a target="_top" ' +
        'href="ftp://ftp.rfc-editor.org/in-notes/rfc6241.txt">RFC 6241: ' +
        'Network Configuration Protocol (NETCONF)</a>\n * @see <a ' +
        'target="_top" href="ftp://ftp.rfc-editor.org/in-notes/rfc6242.txt">' +
        'RFC 6242: Using the NETCONF Protocol over Secure Shell (SSH)</a>\n' +
        ' * @see <a target="_top" href="http://www.tail-f.com">Tail-f ' +
        'Systems</a>\n */\npackage ' + strip_first(self.d) + ';'))


class JavaClass(object):
    """Encapsulates package name, imports, class declaration, constructors,
    fields, access methods, etc. for a Java Class. Also includes javadoc
    documentation where applicable.

    Implementation: Unless the 'body' attribute is used, different kind of
    methods and fields are stored in separate dictionaries so that the order of
    them in the generated class does not depend on the order in which they were
    added.

    """

    def __init__(self, filename=None, package=None, imports=None,
                 description=None, body=None, version='1.0',
                 superclass=None, interfaces=None, source='<unknown>.yang'):
        """Constructor.

        filename    -- Should preferably not contain a complete path since it is
                       displayed in a Java comment in the beginning of the code.
        package     -- Should be just the name of the package in which the class
                       will be included.
        imports     -- Should be a list of names of imported libraries.
        description -- Defines the class semantics.
        body        -- Should contain the actual code of the class if it is not
                       supplied through the add-methods
        version     -- Version number, defaults to '1.0'.
        superclass  -- Parent class of this Java class, or None
        interaces   -- List of interfaces implemented by this Java class
        source      -- A string somehow representing the origin of the class

        """
        if imports is None:
            imports = []
        self.filename = filename
        self.package = package
        self.imports = OrderedSet()
        for i in range(len(imports)):
            self.imports.add(imports[i])
        self.description = description
        self.body = body
        self.version = version
        self.superclass = superclass
        self.interfaces = interfaces
        if interfaces is None:
            self.interfaces = []
        self.source = source
        self.fields = OrderedSet()
        self.constructors = OrderedSet()
        self.cloners = OrderedSet()
        self.enablers = OrderedSet()
        self.schema_registrators = OrderedSet()
        self.name_getters = OrderedSet()
        self.access_methods = collections.OrderedDict()
        self.support_methods = OrderedSet()
        self.attrs = [self.fields, self.constructors, self.cloners,
                      self.enablers, self.schema_registrators,
                      self.name_getters, self.access_methods,
                      self.support_methods]

    def add_field(self, field):
        """Adds a field represented as a string"""
        self.fields.add(field)

    def add_constructor(self, constructor):
        """Adds a constructor represented as a string"""
        self.constructors.add(constructor)

    def add_cloner(self, cloner):
        """Adds a clone method represented as a string"""
        if not isinstance(cloner, str):
            for import_ in cloner.imports:
                self.imports.add(import_)
        self.cloners.add(cloner)

    def add_enabler(self, enabler):
        """Adds an 'enable'-method as a string"""
        self.imports.add('com.tailf.jnc.JNCException')
        self.imports.add('com.tailf.jnc.YangElement')
        self.enablers.add(enabler)

    def add_schema_registrator(self, schema_registrator):
        """Adds a register schema method"""
        self.imports.add('com.tailf.jnc.JNCException')
        self.imports.add('com.tailf.jnc.SchemaParser')
        self.imports.add('com.tailf.jnc.Tagpath')
        self.imports.add('com.tailf.jnc.SchemaNode')
        self.imports.add('com.tailf.jnc.SchemaTree')
        self.imports.add('java.util.HashMap')
        self.schema_registrators.add(schema_registrator)

    def add_name_getter(self, name_getter):
        """Adds a keyNames or childrenNames method represented as a string"""
        self.name_getters.add(name_getter)

    def append_access_method(self, key, access_method):
        """Adds an access method represented as a string"""
        if self.access_methods.get(key) is None:
            self.access_methods[key] = []
        self.access_methods[key].append(access_method)

    def add_support_method(self, support_method):
        """Adds a support method represented as a string"""
        self.support_methods.add(support_method)

    def get_body(self):
        """Returns self.body. If it is None, fields and methods are added to it
        before it is returned."""
        if self.body is None:
            self.body = []
            if self.superclass is not None or 'Serializable' in self.interfaces:
                self.body.extend(JavaValue(
                    modifiers=['private', 'static', 'final', 'long'],
                    name='serialVersionUID', value='1L', indent=4).as_list())
                self.body.append('')
            for method in flatten(self.attrs):
                if hasattr(method, 'as_list'):
                    self.body.extend(method.as_list())
                else:
                    self.body.append(method)
                self.body.append('')
            self.body.append('}')
        return self.body

    def get_superclass_and_interfaces(self):
        """Returns a string with extends and implements"""
        res = []
        if self.superclass:
            res.append(' extends ')
            res.append(self.superclass)
        if self.interfaces:
            res.append(' implements ')
            res.append(', '.join(self.interfaces))
        if res:
            res.append(' ')
        return ''.join(res)

    def java_class(self):
        """Returns a string representing complete Java code for this class.

        It is vital that either self.body contains the complete code body of
        the class being generated, or that it is None and methods have been
        added using the JavaClass.add methods prior to calling this method.
        Otherwise the class will be empty.

        The class name is the filename without the file extension.

        """
        # The header is placed in the beginning of the Java file
        header = [' '.join(['/* \n * @(#)' + self.filename,
                            '      ',
                            self.version,
                            get_date(date_format=0)])]
        header.append(' *')
        header.append(' * This file has been auto-generated by JPyang, the')
        header.append(' * Java output format plug-in of pyang.')
        header.append(' * Origin: ' + self.source)
        header.append(' */')

        # package and import statement goes here
        header.append('')
        header.append('package ' + strip_first(self.package) + ';')
        if self.body is None:
            for method in flatten(self.attrs):
                if hasattr(method, 'imports'):
                    self.imports |= method.imports
                if hasattr(method, 'exceptions'):
                    self.imports |= map(lambda s: 'com.tailf.jnc.' + s,
                                        method.exceptions)
        if self.imports:
            header.append('')
            for import_ in self.imports.as_sorted_list():
                header.append('import ' + import_ + ';')

        # Class doc-comment and declaration, with modifiers
        header.append('')
        header.append('/**')
        header.append(' * ' + self.description)
        header.append(' *')
        header.append(' '.join([' * @version',
                                self.version,
                                get_date(date_format=1)]))
        header.append(' * @author Auto Generated')
        header.append(' */')
        header.append(''.join(['public class ',
                               self.filename.split('.')[0],
                               self.get_superclass_and_interfaces(),
                               '{']))
        header.append('')
        return '\n'.join(header + self.get_body())


class JavaValue(object):
    """A Java value"""

    def __init__(self, exact=None, javadocs=None, modifiers=None, name=None,
                 value=None, imports=None, indent=0):
        """Value constructor"""
        self.exact = exact
        self.value = value
        self.indent = ' ' * indent
        
        self.javadocs = []
        if javadocs is not None:
            for javadoc in javadocs:
                self.add_javadoc(javadoc)
        
        self.modifiers = []
        if modifiers is not None:
            for modifier in modifiers:
                self.add_modifier(modifier)
        
        self.name = None
        if name is not None:
            self.set_name(name)
        
        self.imports = set([])
        if imports is not None:
            for import_ in imports:
                self.imports.add(import_)

    def __eq__(self, other):
        """Returns True iff self and other represents an identical value"""
        for attr, value in vars(self).items():
            try:
                if getattr(other, attr) != value:
                    return False
            except AttributeError:
                return False
        return True

    def __ne__(self, other):
        """Returns True iff self and other represents different values"""
        return not self.__eq__(other)

    def shares_mutables_with(self, other):
        """Returns True iff mutable instance data is shared with other"""
        Immutable = basestring, tuple, Number, frozenset
        for attr, value in vars(self).items():
            if value is None or isinstance(value, Immutable):
                continue
            try:
                if getattr(other, attr) is value:
                    return True
            except AttributeError:
                pass
        return False

    def _set_instance_data(self, attr, value):
        try:
            data = getattr(self, attr)
            if isinstance(data, list):
                data.append(value)
            else:
                setattr(self, attr, value)
        except AttributeError:
            print_warning(msg='Unknown attribute: ' + attr, key=attr)
        else:
            self.exact = None  # Invalidate cache

    def set_name(self, name):
        """Sets the identifier of this value"""
        self._set_instance_data('name', name)

    def set_indent(self, indent=4):
        """Sets indentation used in the as_list methods"""
        self._set_instance_data('indent', ' ' * indent)

    def add_modifier(self, modifier):
        """Adds modifier to end of list of modifiers"""
        self._set_instance_data('modifiers', modifier)

    def add_javadoc(self, line):
        """Adds line to javadoc comment, leading ' ', '*' and '/' removed"""
        self._set_instance_data('javadocs', line.lstrip(' */'))

    def add_dependency(self, import_):
        """Adds import_ to list of imports needed for value to compile."""
        _, sep, class_name = import_.rpartition('.')
        if sep:
            if class_name not in java_built_in:
                self.imports.add(import_)
                return class_name
        elif import_ not in java_built_in:
            self.imports.add(import_)
        return import_

    def javadoc_as_string(self):
        """Returns a list representing javadoc lines for this value"""
        lines = []
        if self.javadocs:
            lines.append( self.indent + '/**' )
            lines.extend([self.indent + ' * ' + line for line in self.javadocs])
            lines.append( self.indent + ' */' )
        return lines

    def as_list(self):
        """String list of code lines that this Java value consists of"""
        if self.exact is None:
            assert self.name is not None
            assert self.indent is not None
            self.exact = self.javadoc_as_string()
            declaration = self.modifiers + [self.name]
            if self.value is not None:
                declaration.append('=')
                declaration.append(self.value)
            self.exact.append(''.join([self.indent, ' '.join(declaration), ';']))
        return self.exact


class JavaMethod(JavaValue):
    """A Java method"""

    def __init__(self, exact=None, javadocs=None, modifiers=None,
                 return_type=None, name=None, parameters=None, exceptions=None,
                 body=None, indent=4):
        """Method constructor"""
        super(JavaMethod, self).__init__(exact=exact, javadocs=javadocs,
                                         modifiers=modifiers, name=name,
                                         value=None, indent=indent)
        self.return_type = None
        if return_type is not None:
            self.set_return_type(return_type)
        
        self.parameters = []
        if parameters is not None:
            for param_type, param_name in parameters:
                self.add_parameter(param_type, param_name, stmt=None)
                
        self.exceptions = []
        if exceptions is not None:
            for exc in exceptions:
                self.add_exception(exc)
        
        self.body = body
        if body is None:
            self.body = []

    def set_return_type(self, return_type):
        """Sets the type of the return value of this method"""
        self._set_instance_data('return_type',
                                self.add_dependency(return_type))

    def add_parameter(self, param_type, param_name, stmt=None):
        """Adds a parameter to this method
        
        param_type -- String representation of the argument type
        param_name -- String representation of the argument name
        stmt       -- The statement that the class containing this method is
                      generated from, if any.
        """
        self._set_instance_data('parameters', 
                                ' '.join([self.add_dependency(param_type),
                                          param_name]))

    def add_exception(self, exception):
        """Adds exception to method"""
        self._set_instance_data('exceptions',
                                self.add_dependency(exception))

    def add_line(self, line):
        """Adds line to method body"""
        self._set_instance_data('body', self.indent + ' ' * 4 + line)

    def as_list(self):
        """String list of code lines that this Java method consists of.
        Overrides JavaValue.as_list().

        """
        if self.exact is None:
            assert self.name is not None
            assert self.indent is not None
            self.exact = self.javadoc_as_string()
            header = self.modifiers[:]
            if self.return_type is not None:
                header.append(self.return_type)
            header.append(self.name)
            signature = [self.indent]
            signature.append(' '.join(header))
            signature.append('(')
            signature.append(', '.join(self.parameters))
            signature.append(')')
            if self.exceptions:
                signature.append(' throws ')
                signature.append(', '.join(self.exceptions))
            signature.append(' {')
            self.exact.append(''.join(signature))
            self.exact.extend(self.body)
            self.exact.append(self.indent + '}')
        return self.exact


class MethodGenerator(object):
    """A generator for JavaMethod objects"""

    def __init__(self, stmt, ctx):
        """Sets the attributes of the method generator, depending on stmt"""
        self.stmt = stmt
        self.n = extract_names(stmt.arg)[1]
        prefix = None
        if self.stmt.top is not None:
            prefix = self.stmt.top.search_one('prefix')
        self.root = None
        if prefix is not None:
            self.root = extract_names(prefix.arg)[1]
        self.is_container = stmt.keyword == 'container'
        self.is_list = stmt.keyword == 'list'
        self.is_typedef = stmt.keyword == 'typedef'
        self.is_top_level = self.stmt.parent == self.stmt.top
        assert self.is_container or self.is_list or self.is_typedef
        self.ctx = ctx
        self.gen = self
        if type(self) is MethodGenerator:
            if self.is_typedef:
                self.gen = TypedefMethodGenerator(stmt, ctx)
            if self.is_container:
                self.gen = ContainerMethodGenerator(stmt, ctx)
            if self.is_list:
                self.gen = ListMethodGenerator(stmt, ctx)

    def fix_imports(self, method):
        res = set([])
        children = map(lambda s: capitalize_first(camelize(s.arg)),
                       self.stmt.substmts)
        basepkg = self.ctx.opts.directory
        if basepkg[:4] == 'src' + os.sep:
            basepkg = basepkg[4:]
        for dependency in method.imports:
            if dependency.startswith(('java.math', 'com.tailf.jnc', basepkg)):
                res.add(dependency)
                continue
            elif dependency in ('BigInteger', 'BigDecimal'):
                pkg = 'java.math'
            elif dependency == self.root:
                if self.is_top_level:
                    continue
                pkg = basepkg
            elif dependency in children:
                pkg = '.'.join([basepkg, self.stmt.arg])
            else:
                pkg = 'com.tailf.jnc'
            res.add('.'.join([pkg, dependency]))
        method.imports = res
        return method

    def _root_namespace(self, stmt_arg):
        """Returns '([Root].NAMESPACE, "[stmt.arg]");'"""
        return ['(', self.root, '.NAMESPACE, "', stmt_arg, '");']
    
    def constructor_template(self):
        """Returns a constructor invoking parent constructor, without
        parameters and javadoc."""
        constructor = JavaMethod(modifiers=['public'], name=self.n)
        if self.is_container or self.is_list:
            call = ['super']
            call.extend(self._root_namespace(self.stmt.arg))
            constructor.add_dependency(self.root)
            constructor.add_line(''.join(call))
            if self.stmt.parent == self.stmt.top:
                # Top level statement
                constructor.add_line('setDefaultPrefix();')
                setPrefix = ['setPrefix(', self.root, '.PREFIX);']
                constructor.add_line(''.join(setPrefix))
        else:
            constructor.add_line('super(value);')
        return self.fix_imports(constructor)

    def empty_constructor(self):
        """Returns parameter-free constructor as a JavaMethod object"""
        assert not self.is_typedef, "Typedefs don't have empty constructors"
        constructor = self.constructor_template()
        javadoc = ['Constructor for an empty ']
        javadoc.append(self.n)
        javadoc.append(' object.')
        constructor.add_javadoc(''.join(javadoc))
        return self.fix_imports(constructor)

    def constructors(self):
        """Returns a list of JavaMethods representing constructors to include
        in generated class of self.stmt

        """
        assert self.gen is not self, 'Avoid infinite recursion'
        return self.gen.constructors()

    def cloners(self):
        if self.is_typedef:
            return []  # Typedefs don't have clone methods
        cloners = [JavaMethod(), JavaMethod()]
        a = ('an exact', 'a shallow')
        b = ('', ' Children are not included.')
        c = ('', 'Shallow')
        for i, cloner in enumerate(cloners):
            cloner.add_javadoc('Clones this object, returning %s copy.' % a[i])
            cloner.add_javadoc('@return A clone of the object.%s' % b[i])
            cloner.add_modifier('public')
            cloner.set_return_type('YangElement')
            cloner.set_name('clone%s' % c[i])
            cloner.add_line('return clone%sContent(new %s());' % (c[i], self.n))
            cloner = self.fix_imports(cloner)
        return cloners

    def support_method(self, fields=None):
        
        if self.is_typedef:
            return None
        add_child = JavaMethod(modifiers=['public'],
                               return_type='void',
                               name='addChild',
                               parameters=[('Element', 'child')])
        add_child.add_dependency('Element')
        add_child.add_javadoc('Support method for addChild.')
        add_child.add_javadoc('Adds a child to this object.')
        add_child.add_javadoc('')
        add_child.add_javadoc('@param child The child to add')
        add_child.add_line('super.addChild(child);')
        if fields is None:
            fields = []
        for i in range(len(fields) - 1, -1, -1):
            cond = ''
            if i > 0:
                cond = 'else '
            add_child.add_line(''.join([cond, 'if (child instanceof ',
                    capitalize_first(fields[i]), ') ', fields[i], ' = (',
                    capitalize_first(fields[i]), ')child;']))
            add_child.add_dependency(capitalize_first(fields[i]))
        return self.fix_imports(add_child)

    def setters(self):
        """Returns a list of JavaMethods representing setters to include
        in generated class of self.stmt

        """
        assert self.gen is not self, 'Avoid infinite recursion'
        return self.gen.setters()

    def checker(self):
        assert self.gen is not self, 'Avoid infinite recursion'
        if self.is_typedef:
            return self.gen.checker()
        else:
            return None
    
    def markers(self):
        """Generates methods that enqueues operations to be performed."""
        if self.is_typedef:
            return None
        else:
            return self.gen.markers()


class LeafMethodGenerator(object):
    """Method generator for YANG leaf and leaf-list associated methods"""
    
    def __init__(self, stmt, ctx):
        self.stmt = stmt
        self.is_leaf = stmt.keyword == 'leaf'
        self.is_leaflist = stmt.keyword == 'leaf-list'
        assert self.is_leaf or self.is_leaflist
        self.stmt_type = stmt.search_one('type')
        self.type_str = get_types(self.stmt_type, ctx)
        self.is_string = self.type_str[1] == 'String'
        self.ctx = ctx
        
    def mark(self, op):
        assert op in ('replace', 'merge', 'create', 'delete')
        mark_methods = [JavaMethod()]
        if not self.is_string and self.is_leaflist:
            mark_methods.append(JavaMethod())
        for i, mark_method in enumerate(mark_methods):
            mark_method.set_name('mark' + capitalize_first(self.stmt.arg)
                                 + capitalize_first(op))
            mark_method.add_modifier('public')
            mark_method.set_return_type('void')
            mark_method.add_exception('JNCException')
            path = self.stmt.arg
            mark_method.add_javadoc(''.join(['Marks the ', self.stmt.keyword,
                                             ' "', self.stmt.arg,
                                             '" with operation "', op, '".']))
            if self.is_leaflist:
                path += '[name=\'" + ' + self.stmt.arg + 'Value + "\']'
                javadoc = '@param ' + self.stmt.arg + 'Value The value to mark'
                param_type = self.type_str[0]
                if i == 1:
                    javadoc += ', given as a String'
                    param_type = 'String'
                mark_method.add_parameter(param_type, self.stmt.arg + 'Value')
                mark_method.add_javadoc(javadoc)
            mark_method.add_line('markLeaf' + capitalize_first(op) + '("' + path + '");')
#            mark_method = self.fix_imports(mark_method)
        return mark_methods


class TypedefMethodGenerator(MethodGenerator):
    """Method generator specific to typedef classes"""

    def __init__(self, stmt, ctx=None):
        super(TypedefMethodGenerator, self).__init__(stmt, ctx)
        assert self.gen is self
        assert self.is_typedef, 'This class is only valid for typedef stmts'
        self.stmt_type = stmt.search_one('type')  # FIXME use get_type!
        self.is_string = False
        self.needs_check = True  # Set to False to avoid redundant checks
        if self.stmt_type is not None:
            self.is_string = self.stmt_type.arg in ('string', 'enumeration')   # FIXME use get_type!
            for s in ('length', 'path', 'range', 'require_instance'):
                setattr(self, s, self.stmt_type.search_one(s))
            for s in ('bit', 'enum', 'pattern'):
                setattr(self, s, self.stmt_type.search(s))
            # self.needs_check = self.enum or self.pattern

    def constructors(self):
        """Returns a list containing a single or a pair of constructors"""
        constructors = []
        primitive = get_types(self.stmt_type, self.ctx)[1]
        javadoc = ['@param value Value to construct the ', self.n, ' from.']

        # Iterate once if string, twice otherwise
        for i in range(1 + (not self.is_string)):
            constructor = self.constructor_template()
            javadoc2 = ['Constructor for ', self.n]
            if i == 0:
                # String constructor
                javadoc2.append(' object from a string.')
                constructor.add_parameter('String', 'value')
            else:
                # i == 1, Primitive constructor
                javadoc2.extend([' object from a ', primitive, '.'])
                tmp_primitive = constructor.add_dependency(primitive)
                constructor.add_parameter(tmp_primitive, 'value')
            constructor.add_javadoc(''.join(javadoc2))
            constructor.add_javadoc(''.join(javadoc))
            if self.needs_check:
                constructor.add_line('check();')
                exception = constructor.add_dependency('YangException')
                constructor.add_exception(exception)
            constructors.append(self.fix_imports(constructor))
        return constructors

    def setters(self):
        """Returns a list of set_value JavaMethods"""
        setters = []
        primitive = get_types(self.stmt_type, self.ctx)[1]
        javadoc = '@param value The value to set.'

        # Iterate once if string, twice otherwise
        for i in range(1 + (not self.is_string)):
            setter = JavaMethod(modifiers=['public', 'void'], name='setValue')
            javadoc2 = ['Sets the value using a ']
            if i == 0:
                # String setter
                javadoc2.append('string value.')
                setter.add_parameter('String', 'value')
            else:
                # i == 1, Primitive setter
                javadoc2.extend(['value of type ', primitive, '.'])
                setter.add_parameter(primitive, 'value')
            setter.add_javadoc(''.join(javadoc2))
            setter.add_javadoc(javadoc)
            setter.add_line('super.setValue(value);')
            if self.needs_check:
                setter.add_line('check();')
                setter.add_exception('YangException')
            setters.append(self.fix_imports(setter))
        return setters

    def checker(self):
        """Returns a 'check' JavaMethod, which checks regexp constraints"""
        if self.needs_check:
            checker = JavaMethod(modifiers=['public', 'void'], name='check')
            checker.add_javadoc('Checks all restrictions (if any).')
            checker.add_exception('YangException')
            if self.enum:
                checker.add_line('boolean e = false;')
                for e in self.enum:
                    checker.add_line('e |= enumeration("' + e.arg + '");')
                checker.add_line('throwException( !e );')
            if self.pattern:
                if len(self.pattern) == 1:
                    checker.add_line('pattern("' + self.pattern[0].arg + '");')
                else:
                    checker.add_line('java.lang.String[] regexs = {')
                    for p in self.pattern:
                        checker.add_line('    "' + p.arg + '",')
                    checker.add_line('};')
                    checker.add_line('pattern(regexs);')
            return [self.fix_imports(checker)]
        return []


class ContainerMethodGenerator(MethodGenerator):
    """Method generator specific to classes generated from container stmts"""

    def __init__(self, stmt, ctx=None):
        super(ContainerMethodGenerator, self).__init__(stmt, ctx)
        assert self.gen is self
        assert self.is_container, 'Only valid for container stmts'

    def constructors(self):
        return [self.empty_constructor()]

    def setters(self):
        return NotImplemented
    
    def markers(self):
        return NotImplemented
    
    def parent_adder(self, args, string):
        """Generates add-method for stmt, optionally parametrized by an
        argument of specified type and with customizable comments.
    
        stmt   -- The YANG statement that needs an adder
        args   -- A list of tuples, each tuple containing an arg_type and an
                  arg_name. Each arg_type corresponds to an argument type
                  which is also used to deduce what the method should do:
                  1. If arg_type is the same as stmt.arg, the produced method
                     will add its argument as a child instead of creating a new
                     one. No cloning occurs.
                  2. JNC types produces a method that adds a new stmt with that
                     key, unless string is set.
                  Similarly, Each arg_name corresponds to an argument name. The names of
                  the method's arguments are typically key identifiers or a single
                  lowercase stmt name. Setting args to an empty list produces a
                  method with no argument, which can be used to create subtree
                  filters.
        string -- If set to True, the keys are specified with the ordinary String
                  type instead of the Tail-f ConfM String type.
    
        """
        res = [JavaMethod()]
        name = capitalize_first(self.stmt.arg)
        for method in res:
            method.add_modifier('public')
            method.set_return_type(name, self.stmt.parent)
            method.set_name('add' + name)
            method.add_exception('JNCException')
            if args is None:
                args = []
            spec2 = spec3 = ''
            if len(args) == 1 and args[0][0] == self.stmt.arg:
                spec1 = ('.\n     * @param ' + args[0][1] +
                    ' Child to be added to children')
                spec2 = name + ' ' + self.stmt.arg + ', '
            else:
                spec3 = ('\n' + ' ' * 8 + name + ' ' + self.stmt.arg +
                    ' = new ' + name + '(')
                if not args:
                    spec1 = '''.
             * This method is used for creating subtree filters'''
                    spec3 += ');'
                else:
                    spec1 = ', with given key arguments'
                    if string:
                        spec1 += '.\n     * The keys are specified as strings'
                    for (arg_type, arg_name) in args:
                        spec1 += ('.\n     * @param ' + arg_name +
                            ' Key argument of child')
                        if string:
                            spec2 += 'String ' + arg_name + ', '
                        else:
                            spec2 += arg_type + ' ' + arg_name + ', '
                        spec3 += arg_name + ', '
                    spec3 = spec3[:-2] + ');'
            if self.stmt.parent.keyword == 'container':  # Add to a parent field
                spec3 += '\n' + ' ' * 8 + 'this.' + self.stmt.arg + ' = ' + self.stmt.arg + ';'
        return self.fix_imports(res)
        '''    /**
             * Adds ''' + self.stmt.keyword + ' entry "' + self.stmt.arg + '"' + spec1 + '''.
             * @return The added child.
             */
            public ''' + name + ' add' + name + '(' + spec2[:-2] + ''')
                throws JNCException {''' + spec3 + '''
                insertChild(''' + self.stmt.arg + ''', childrenNames());
                return ''' + self.stmt.arg + ''';
            }'''
    
    def parent_access_methods(self):
        res = []
        res.append(access_methods_comment(self.stmt))
        res.append(add_stmt(self.stmt,
                            args=[(self.stmt.parent.arg + '.' +
                                   self.stmt.arg, self.stmt.arg)],
                            field=True))
        res.append(add_stmt(self.stmt, args=[], field=True))
        res.append(delete_stmt(self.stmt))
        return res


class ListMethodGenerator(MethodGenerator):
    """Method generator specific to classes generated from list stmts"""

    def __init__(self, stmt, ctx=None):
        super(ListMethodGenerator, self).__init__(stmt, ctx)
        assert self.gen is self
        assert self.is_list, 'Only valid for list stmts'
        self.is_config = is_config(stmt)

    def value_constructors(self):
        """Returns a list of constructors for configuration data lists"""
        assert self.is_config, 'Only called with configuration data stmts'

        keys = self.stmt.search_one('key').arg.split(' ')
        key_stmts = map(lambda k: self.stmt.search_one('leaf', k), keys)
        constructors = []

        # Determine number of constructors
        number_of_value_constructors = 2
        javadoc1 = ['Constructor for an initialized ', self.n, ' object,']
        javadoc2 = ['', 'with Strings for the keys.']
        if filter(lambda k: k.arg != 'string', key_stmts):
            number_of_value_constructors += 1
            javadoc2.append('with primitive Java types.')

        # Create constructors in a loop
        for i in range(number_of_value_constructors):
            constructor = self.constructor_template()
            constructor.add_javadoc(''.join(javadoc1))
            constructor.add_javadoc(javadoc2[i])
            constructor.add_exception('JNCException')  # TODO: Add only if needed
            for key in key_stmts:
                javadoc = ['@param ', key.arg, 'Value Key argument of child.']
                confm, primitive = get_types(key, self.ctx)
                setValue = [key.arg, '.setValue(']
                if i == 0:
                    # Default constructor
                    param_type = confm
                    setValue.extend([key.arg, 'Value);'])
                else:
                    # String or primitive constructor
                    setValue.extend(['new ', confm, '(', key.arg, 'Value));'])
                    if i == 1:
                        param_type = 'String'
                    else:
                        param_type = primitive
                newLeaf = ['Leaf ', key.arg, ' = new Leaf']
                newLeaf.extend(self._root_namespace(key.arg))
                insertChild = ['insertChild(', key.arg, ', childrenNames());']
                constructor.add_javadoc(''.join(javadoc))
                constructor.add_parameter(param_type, key.arg + 'Value')
                constructor.add_line(''.join(newLeaf))
                constructor.add_line(''.join(setValue))
                constructor.add_line(''.join(insertChild))
            constructors.append(self.fix_imports(constructor))

        return constructors

    def constructors(self):
        # Number of constructors depends on the type of the key
        constructors = [self.empty_constructor()]
        if self.is_config or self.stmt.search_one('key') is not None:
            constructors.extend(self.value_constructors())
        return constructors

    def setters(self):
        return NotImplemented
    
    def markers(self):
        return NotImplemented
    
    def parent_access_methods(self):
        res = []
        _, _, confm_keys, _ = extract_keys(self.stmt, self.ctx)
        res.append(access_methods_comment(self.stmt))
        res.append(get_stmt(self.stmt, confm_keys))
        res.append(get_stmt(self.stmt, confm_keys, string=True))
        res.append(child_iterator(self.stmt))
        res.append(add_stmt(self.stmt, args=[(self.stmt.arg, self.stmt.arg)]))
        res.append(add_stmt(self.stmt, args=confm_keys))
        res.append(add_stmt(self.stmt, args=confm_keys, string=True))
        res.append(add_stmt(self.stmt, args=[]))
        res.append(delete_stmt(self.stmt, args=confm_keys))
        res.append(delete_stmt(self.stmt, args=confm_keys, string=True))
        return res


def constructor(stmt, ctx, root='', set_prefix=False, mode=0, args=None,
    throws=''):
    """The constructor function returns a string representing Java code for a
    constructor of a Java class corresponding to the stmt parameter.

    stmt        -- Typically a module, submodule, container or list Statement
    ctx         -- Context used to identify debug and verbose flags
    root        -- Path to class containing the XML namespace prefix of the
                   YANG module. If not set stmt name will be used.
    set_prefix  -- Set to true to add setDefaultPrefix and setPrefix calls
    mode        -- 0: No arguments
                   1: ConfM arguments
                   2: String arguments
                   3: Java primitive arguments
    args        -- A list of tuples containing the argument type(s) and
                   name(s). If empty, the method will have no argument. The
                   argument name(s) should be supplied without the 'Value'
                   suffix. Typically used as a (set of) key(s) in the method.
                   Note that mode must be > 0 for this to have an effect.
    throws      -- Typically 'throws JNCException', prepended with a newline
                   and spaces for indentation.

    """
    name = capitalize_first(stmt.arg)
    setters = docstring = inserts = arguments = ''
    if args is None:
        args = []
    MAX_COLS = 80 - len('    public ' + name + '(')
    if root == '':
        root = name
    if set_prefix:
        setters = '''
        setDefaultPrefix();
        setPrefix(''' + root + '.PREFIX);'
    if not args or mode == 0:
        obj_status = 'empty '
    else:
        obj_status = 'initialized '
        values = []
        if mode == 1:  # ConfM arguments
            for (arg_type, arg_name) in args:
                values.append(arg_name + 'Value')
        else:  # String or Java primitive arguments
            if mode == 2:
                docstring = '\n     * with Strings for the keys.'
            else:
                docstring = '\n     * with primitive Java types.'
            for (arg_type, arg_name) in args:
#                tmp_list_confm = []
#                get_types(arg_type, tmp_list_confm, [], ctx)
#                decl = 'new ' + tmp_list_confm[0][0] + '('
                decl = 'new String('  # FIXME: should call get_types with a stmt
                values.append(decl + arg_name + 'Value)')
        for (arg_type, arg_name), value in zip(args, values):
            # TODO: http://en.wikipedia.org/wiki/Loop_unswitching

            inserts += ('''
        // Set key element: ''' + arg_name + '''
        Leaf ''' + arg_name + ' = new Leaf(' + root + '.NAMESPACE, "' +
            arg_name + '''");
        ''' + arg_name + '.setValue(' + value + ''');
        insertChild(''' + arg_name + ', childrenNames());')
            arg_name = arg_name + 'Value'
            docstring += ('\n     * @param ' + arg_name +
                ' Key argument of child.')
            if mode == 2:  # String arguments
                tmp = 'String ' + arg_name + ', '
            else:  # ConfM or Java primitive arguments
                tmp = arg_type + ' ' + arg_name + ', '
            if len(tmp) > MAX_COLS:
                tmp = '\n' + ' ' * 8 + tmp
                MAX_COLS = 80 - len(tmp)
            else:
                MAX_COLS -= len(tmp)
            arguments += tmp
        arguments = arguments[:-2]  # Skip the last ', '
    return ('''    /**
     * Constructor for an ''' + obj_status + name + ' object.' + docstring +
     '''
     */
    public ''' + name + '(' + arguments + ')' + throws + ''' {
        super(''' + root + '.NAMESPACE, "' + stmt.arg + '");' +
        inserts + setters + '''
    }''')


def clone(class_name, key_names=None, shallow='False'):
    """Returns a string representing a Java clone method. Iff key_names is
    empty, get<key_name>Value() methods are called to fetch constructor
    arguments and null is returned if an JNCException is raised. If shallow is
    set, the cloneShallowContent method is invoked instead of cloneContent.

    class_name -- The name of the class to clone instances of
    key_names  -- Key identifier(s)
    shallow    -- Boolean flag for which clone method to use

    """
    try_stmt = children = cast = ''
    if key_names is None:
        key_names = []
    if key_names:
        try_stmt = 'try {\n' + ' ' * 12
        catch_stmt = '('
        MAX_COLS = 80 - 44 - len(class_name)
        for key_name in key_names:
            tmp = 'get' + key_name + 'Value(), '
            if len(tmp) > MAX_COLS:
                tmp = '\n' + ' ' * 16 + tmp
                MAX_COLS = 80 - len(tmp)
            else:
                MAX_COLS -= len(tmp)
            catch_stmt += tmp
        catch_stmt = catch_stmt[:-2] + '''));
        } catch (JNCException e) { return null; }
    }\n'''
    else:
        catch_stmt = '());\n    }\n'
    if not shallow:
        copy = 'n exact'
        signature = 'Object clone()'
        cast = '(' + class_name + ')'  # TODO: Not always required
        method = 'cloneContent'
    else:
        copy = ' shallow'
        children = ' Children are not included.'
        signature = 'Element cloneShallow()'
        method = 'cloneShallowContent'
    return ('''    /**
     * Clones this object, returning a''' + copy + ''' copy.
     * @return A clone of the object.''' + children + '''
     */
    public ''' + signature + ''' {
        ''' + try_stmt + 'return ' + cast + method + '(new ' + class_name +
            catch_stmt)


def key_names(stmt):
    """Returns a string representing a Java method that returns a String[]
    with the identifiers of the keys in stmt. If stmt does not have any keys,
    null is returned.

    stmt -- A pyang Statement, typically a list or a container

    """
    keys = stmt.search('key')
    if not keys:
        res = 'return null'
    else:
        res = 'return new String[] {\n'

        # Add keys to res, one key per line, indented by 12 spaces
        for key_str in keys:
            for key in key_str.arg.split(' '):
                res += ' ' * 12 + '"' + key + '",\n'
        res = res[:-2] + '\n' + ' ' * 8 + '}'
    return '''    /**
     * Structure information which specifies
     * the keys for the list entries.
     */
    public String[] keyNames() {
        ''' + res + ''';
    }'''  # TODO: Add support for multiple keys


def children_names(stmt):
    """Returns a string representing a java method that returns a String[]
    with the identifiers of the children of stmt, excluding any keys.

    stmt -- A pyang Statement, typically a list or a container

    """
    children = filter(lambda x:  # x.keyword != 'key' and # TODO: add more
        x.keyword != 'key',
        stmt.substmts)
    names = [ch.arg for ch in children]
    if len(names) > 0:
        names = repr(names)[1:-1].replace("'", '"').replace(', ',
            ',\n' + ' ' * 12)
    else:
        names = ''
    return '''    /**
     * Structure information with the names of the children.
     * Makes it possible to order the children.
     */
    public String[] childrenNames() {
        return new String[] {
            ''' + names + '''
        };
    }'''  # FIXME: What if there are no children?


def static_string(identifier, value):
    """Returns a string representing java code for two fields"""
    return '''    public static final String ''' + identifier + ' = "' + value + '";'


def enable(prefix_name):
    """Returns a string representing a java method that calls the
    YangElement.setPackage method of the confm library, and the registerSchema
    method of the class with prefix == prefix_name (typically = this).

    prefix_name -- The name of the class containing the registerSchema method

    """
    return '''    /**
     * Enable the elements in this namespace to be aware
     * of the data model and use the generated classes.
     */
    public static void enable() throws JNCException {
        YangElement.setPackage(NAMESPACE, PREFIX);
        ''' + prefix_name + '''.registerSchema();
    }'''


def register_schema(prefix_name):
    """Returns a string representing a java method that creates a SchemaParser
    and calls its readFile method with the schema corresponding to the class
    and a hashtable obtained from a call to CsTree.create (a method in the
    ConfM library).

    prefix_name -- The name of the class containing the registerSchema method

    """
    return ('''    /**
     * Register the schema for this namespace in the global
     * schema table (CsTree) making it possible to lookup
     * CsNode entries for all tagpaths
     */
    public static void registerSchema() throws JNCException {
        StackTraceElement[] sTrace = (new Exception()).getStackTrace();
        ClassLoader loader = sTrace[0].getClass().getClassLoader();
        java.net.URL schemaUrl = loader.getResource("''' +
            prefix_name + '''.schema");
        SchemaParser parser = new SchemaParser();
        HashMap<Tagpath, SchemaNode> h = SchemaTree.create(NAMESPACE);
        if (schemaUrl == null)
            parser.readFile("''' + prefix_name + '''.schema", h);
        else
            parser.readFile(schemaUrl, h);
    }''')


def access_methods_comment(stmt, optional=False):
    """Returns a string representing a java comment for code structure"""
    if optional:
        opt = 'optional '
    else:
        opt = ''
    return ('''    /**
     * -------------------------------------------------------
     * Access methods for ''' + opt + stmt.keyword +
     ' child: "' + stmt.arg + '''".
     * -------------------------------------------------------
     */''')


def child_field(stmt):
    """Returns a string representing java code for a field"""
    return '''    /**
     * Field for child ''' + stmt.keyword + ' "' + stmt.arg + '''".
     */
    public ''' + capitalize_first(stmt.arg) + ' ' + stmt.arg + ''' = null;'''


def get_stmt(stmt, keys, string=False):
    """Get method generator. Similar to add_stmt (see below), but does not
    allow parameter-free methods to be generated.

    stmt   -- Typically a list statement
    keys   -- A list of key types and the corresponding identifiers
    string -- if set to True, keys are specified as Strings in method

    """
    name = capitalize_first(stmt.arg)
    spec = arguments = xpath = ''
    if string:
        spec = '\n     * The keys are specified as Strings'
    for (key_type, key_name) in keys:
        spec += '\n     * @param ' + key_name + ' Key argument of child.'
        if string:
            arguments += 'String ' + key_name + ', '
        else:
            arguments += key_type + ' ' + key_name + ', '
        xpath += '[' + key_name + '=\'" + ' + key_name + ' + "\']'
    return ('''    /**
     * Get method for ''' + stmt.keyword + ' entry: "' + stmt.arg + '''".
     * Return the child with the specified keys ''' +
     '(if any).' + spec + '''
     * @return The ''' + stmt.keyword + ''' entry with the specified keys.
     */
    public ''' + name + ' get' + name + '(' + arguments[:-2] + ''')
        throws JNCException {
        String path = "''' + stmt.arg + xpath + '''";
        return (''' + name + ''')searchOne(path);
    }''')


def get_value(stmt, ret_type='com.tailf.confm.xs.String'):
    """get<Identifier>Value method generator. Similar to get_stmt (see below),
    but allows parameter-free methods to be generated.

    stmt     -- Typically a leaf statement
    ret_type -- The type of the return value of the generated method

    """
    name = capitalize_first(stmt.arg)
    return '''    /**
     * Return the value for child ''' + stmt.keyword + ' "' + stmt.arg + '''".
     * @return The value of the ''' + stmt.keyword + '''.
     */
    public ''' + ret_type + ' get' + name + '''Value()
        throws JNCException {
        return (''' + ret_type + ')getValue("' + stmt.arg + '''");
    }'''


def set_leaf_value(stmt, prefix='', arg_type='', confm_type=''):
    """set<Identifier>Value method generator, specifically for leafs.

    stmt       -- Typically a leaf statement
    prefix     -- Namespace prefix of module, empty if the setLeafValue or
                  setLeafListValue methods are not to be used in the method
    arg_type   -- Type of method parameter, empty if parameter free
    confm_type -- Type to use internally, empty if the setIdValue method is not
                  to be used in the method

    """
    name = capitalize_first(stmt.arg)
    spec1 = spec2 = ''
    MAX_COLS = 80 - len(('     * Sets the value for child ' + stmt.keyword +
        ' "' + stmt.arg + '",.'))  # Space left to margin

    # Add different comments depending on argument type
    if arg_type == 'String':
        spec1 = ', using a string value'
        spec2 = 'string representation of the '
    elif arg_type == '':
        pass
    else:
        spec1 = ', using the java primitive value'

    # Do linebreak if neccessary
    if len(spec1) > MAX_COLS:
        spec1 = ',\n     * ' + spec1[2:]

    # Register namespace if prefix is not empty, else use generated set-method
    if prefix:
        if stmt.keyword == 'leaf-list':
            body = 'setLeafListValue('
        else:
            body = 'setLeafValue('
        body += prefix + '''.NAMESPACE,
            "''' + stmt.arg + '''",
            ''' + stmt.arg + '''Value,
            childrenNames());'''
    else:
        body = ('set' + name + 'Value(new ' + confm_type + '(' + stmt.arg +
            'Value));')

    # Prepare method argument listing
    argument = arg_type + ' ' + stmt.arg + 'Value'
    if arg_type == '':
        # Special case of no argument

        argument = ''
        body = 'set' + name + 'Value(new ' + name + '());'
    return set_value(stmt, name, spec1, spec2, argument, body)


def set_value(stmt, nameID='', spec1='', spec2='', argument='', body=''):
    """set<Identifier>Value method generator.

    stmt     -- The statement to set the value for
    nameID   -- Identifier in method name
    spec1    -- Text to insert before parameter listing
    spec2    -- parameter description
    argument -- Full argument listing of method
    body     -- The code to be put in the method body

    """
    if argument:
        spec1 += '''.
     * @param ''' + stmt.arg + 'Value The ' + spec2 + 'value to set'
    return ('''    /**
     * Sets the value for child ''' + stmt.keyword + ' "' + stmt.arg + '"' +
        spec1 + '''.
     */
    public void set''' + nameID + 'Value(' + argument + ''')
        throws JNCException {
        ''' + body + '''
    }''')


def unset_value(stmt):
    """unset<Identifier> method generator"""
    return '''    /**
     * Unsets the value for child ''' + stmt.keyword + ' "' + stmt.arg + '''".
     */
    public void unset''' + capitalize_first(stmt.arg) + '''Value()
        throws JNCException {
        delete("''' + stmt.arg + '''");
    }'''


def add_value(stmt, prefix):
    """add<Identifier> method generator, designed for leaf statements. Not to
    be confused with the add_stmt function which is similar but does not
    contain a call to the setLeafValue function.

    stmt   -- Typically a leaf statement
    prefix -- Namespace prefix of module

    """
    name = value_type = ''
    if stmt.keyword == 'leaf-list':
        name = 'Empty'
        value_type = 'List'
    name += capitalize_first(stmt.arg)
    return ('''    /**
     * This method is used for creating a subtree filter.
     * The added "''' + stmt.arg + '" ' + stmt.keyword +
        ''' will not have a value.
     */
    public void add''' + name + '''()
        throws JNCException {
        setLeaf''' + value_type + 'Value(' + prefix + '''.NAMESPACE,
            "''' + stmt.arg + '''",
            null,
            childrenNames());
    }''')


def mark(stmt, op, arg_type='String'):
    """Generates a method that enqueues an operation to be performed on an
    element.

    stmt     -- Typically a leaf or leaf-list statement. If stmt is not a leaf-
                list, the method will have no argument.
    op       -- The operation 'replace', 'merge', 'create' or 'delete'
    arg_type -- Type of argument, if any.

    """
    spec = op + '"'
    path = stmt.arg
    argument = ''
    if stmt.keyword == 'leaf-list':
        spec += '''.
     * @param ''' + stmt.arg + 'Value The value to mark'
        if arg_type == 'String':
            spec += ', given as a String'
        argument = arg_type + ' ' + stmt.arg + 'Value'
        path += '[name=\'" + ' + stmt.arg + 'Value+"\']'
    return ('''    /**
     * Marks the "''' + stmt.arg + '" ' + stmt.keyword +
        ' with operation "' + spec + '''.
     */
    public void mark''' + capitalize_first(stmt.arg) + capitalize_first(op) +
        '(' + argument + ''')
        throws JNCException {
        markLeaf''' + capitalize_first(op) + '("' + path + '''");
    }''')


def child_iterator(substmt):
    """Returns a string representing a java iterator method for the substmt"""
    if substmt.keyword == 'leaf-list':
        iterator_type = 'LeafListValue'
    else:
        iterator_type = 'Children'
    return ('''    /**
     * Iterator method for the ''' + substmt.keyword + ' "' + substmt.arg +
        '''".
     * @return An iterator for the ''' + substmt.keyword + '''.
     */
    public Element''' + iterator_type + 'Iterator ' + substmt.arg +
        '''Iterator() {
        return new Element''' + iterator_type + 'Iterator(children, "' +
        substmt.arg + '''");
    }''')


def add_stmt(stmt, args=None, field=False, string=False):
    """Generates add-method for stmt, optionally parametrized by an
    argument of specified type and with customizable comments.

    stmt   -- The YANG statement that needs an adder
    args   -- A list of tuples, each tuple containing an arg_type and an
              arg_name. Each arg_type corresponds to a method argument type
              which is also used to deduce what the method should do:
              1. If arg_type is the same as stmt.arg, the produced method
                 will add its argument as a child instead of creating a new
                 one. No cloning occurs.
              2. Tail-f ConfM types produces a method that adds a new stmt
                 with that key of the Tail-f String type, unless string is set.
              Each arg_name corresponds to a method argument name. The names of
              the method's arguments are typically key identifiers or a single
              lowercase stmt name. Setting args to an empty list produces a
              method with no argument, which can be used to create subtree
              filters.
    string -- If set to True, the keys are specified with the ordinary String
              type instead of the Tail-f ConfM String type.
    field  -- If set to True, the statement is added to a field, typically
              in a class representing a YANG container element.

    """
    if args is None:
        args = []
    name = capitalize_first(stmt.arg)
    spec2 = spec3 = ''
    if len(args) == 1 and args[0][0] == stmt.arg:
        spec1 = ('.\n     * @param ' + args[0][1] +
            ' Child to be added to children')
        spec2 = name + ' ' + stmt.arg + ', '
    else:
        spec3 = ('\n' + ' ' * 8 + name + ' ' + stmt.arg +
            ' = new ' + name + '(')
        if not args:
            spec1 = '''.
     * This method is used for creating subtree filters'''
            spec3 += ');'
        else:
            spec1 = ', with given key arguments'
            if string:
                spec1 += '.\n     * The keys are specified as strings'
            for (arg_type, arg_name) in args:
                spec1 += ('.\n     * @param ' + arg_name +
                    ' Key argument of child')
                if string:
                    spec2 += 'String ' + arg_name + ', '
                else:
                    spec2 += arg_type + ' ' + arg_name + ', '
                spec3 += arg_name + ', '
            spec3 = spec3[:-2] + ');'
    if field:
        spec3 += '\n' + ' ' * 8 + 'this.' + stmt.arg + ' = ' + stmt.arg + ';'
    return '''    /**
     * Adds ''' + stmt.keyword + ' entry "' + stmt.arg + '"' + spec1 + '''.
     * @return The added child.
     */
    public ''' + name + ' add' + name + '(' + spec2[:-2] + ''')
        throws JNCException {''' + spec3 + '''
        insertChild(''' + stmt.arg + ''', childrenNames());
        return ''' + stmt.arg + ''';
    }'''


def delete_stmt(stmt, args=None, string=False, keys=True):
    """Delete method generator. Similar to add_stmt (see above).

    stmt   -- Typically a list or container statement
    args   -- A list of tuples, each tuple containing an arg_type and an
              arg_name. Each arg_type corresponds to a method argument type.
              Each arg_name corresponds to a method argument name. The names of
              the method's arguments are typically key identifiers or a single
              lowercase stmt name. Setting args to an empty list produces a
              method with no argument.
    string -- If set to True, the keys are specified with the ordinary String
              type instead of the Tail-f ConfM String type.

    """
    spec1 = spec2 = spec3 = arguments = ''
    if args is None:
        args = []
    if args:
        if keys:
            spec1 = '", with specified keys.'
            if string:
                spec1 += '\n     * The keys are specified as Strings'
        elif string:
            spec1 += '\n     * The value is specified as a String'
        for (arg_type, arg_name) in args:
            # TODO: http://en.wikipedia.org/wiki/Loop_unswitching

            if string:
                arguments += 'String ' + arg_name + ', '
            else:
                arguments += arg_type + ' ' + arg_name + ', '
            if keys:
                spec1 += ('\n     * @param ' + arg_name +
                    ' Key argument of child.')
                spec3 += '[' + arg_name + '=\'" + ' + arg_name + ' + "\']'
            else:
                spec1 += ('\n     * @param ' + arg_name +
                    ' Child to be removed.')
                spec3 += '[name=\'" + ' + arg_name + ' + "\']'
    else:
        spec1 = ''
        spec2 = 'this.' + stmt.arg + ' = null;\n        '
    return '''    /**
     * Deletes ''' + stmt.keyword + ' entry "' + stmt.arg + spec1 + '''"
     */
    public void delete''' + capitalize_first(stmt.arg) + '(' + arguments[:-2] + ''')
        throws JNCException {
        ''' + spec2 + 'String path = "' + stmt.arg + spec3 + '''";
        delete(path);
    }'''


class OrderedSet(collections.MutableSet):
    """A set of unique items that preserves the insertion order.

    Created by: Raymond Hettinger 2009-03-19
    Edited by: Emil Wall 2012-08-03
    Licence: http://opensource.org/licenses/MIT
    Original source: http://code.activestate.com/recipes/576694/

    An ordered set is implemented as a wrapper class for a dictionary
    implementing a doubly linked list. It also has a pointer to the last item
    in the set (self.end) which is used by the add and _iterate methods to add
    items to the end of the list and to know when an iteration has finished,
    respectively.

    """

    def __init__(self, iterable=None):
        """Creates an ordered set.

        iterable -- A mutable iterable, typically a list or a set, containing
                    initial values of the set. If the default value (None) is
                    used, the set is initialized as empty.

        """
        self.ITEM, self.PREV, self.NEXT = range(3)
        self.end = end = []
        end += [None, end, end]         # sentinel node for doubly linked list
        self.map = {}                   # value --> [value, prev, next]
        if iterable is not None:
            self |= iterable

    def __len__(self):
        """Returns the number of items in this set."""
        return len(self.map)

    def __contains__(self, item):
        """Returns true if item is in this set; false otherwise."""
        return item in self.map

    def add(self, item):
        """Adds an item to the end of this set."""
        if item not in self:
            self.map[item] = [item, self.end[self.PREV], self.end]
            self.end[self.PREV][self.NEXT] = self.map[item]
            self.end[self.PREV] = self.map[item]

    def add_first(self, item):
        """Adds an item to the beginning of this set."""
        if item not in self:
            self.map[item] = [item, self.end, self.end[self.NEXT]]
            self.end[self.NEXT][self.PREV] = self.map[item]
            self.end[self.NEXT] = self.map[item]

    def discard(self, item):
        """Finds and discards an item from this set, amortized O(1) time."""
        if item in self:
            item, prev, after = self.map.pop(item)
            prev[self.NEXT] = after
            after[self.PREV] = prev

    def _iterate(self, iter_index):
        """Internal generator method to iterate through this set.

        iter_index -- If 1, the set is iterated in reverse order. If 2, the set
                      is iterated in order of insertion. Else IndexError.

        """
        curr = self.end[iter_index]
        while curr is not self.end:
            yield curr[self.ITEM]
            curr = curr[iter_index]

    def __iter__(self):
        """Returns a generator object for iterating the set in the same order
        as its items were added.

        """
        return self._iterate(self.NEXT)

    def __reversed__(self):
        """Returns a generator object for iterating the set, beginning with the
        most recently added item and ending with the first/oldest item.

        """
        return self._iterate(self.PREV)

    def pop(self, last=True):
        """Discards the first or last item of this set.

        last -- If True the last item is discarded, otherwise the first.

        """
        if not self:
            raise KeyError('set is empty')
        item = next(reversed(self)) if last else next(iter(self))
        self.discard(item)
        return item
    
    def as_sorted_list(self):
        """Returns a sorted list with the items in this set"""
        res = [x for x in self]
        res.sort()
        return res

    def __repr__(self):
        """Returns a string representing this set. If empty, the string
        returned is 'OrderedSet()', otherwise if the set contains items a, b
        and c: 'OrderedSet([a, b, c])'

        """
        if not self:
            return '%s()' % (self.__class__.__name__,)
        return '%s(%r)' % (self.__class__.__name__, list(self))

    def __eq__(self, other):
        """Returns True if other is an OrderedSet containing the same items as
        other, in the same order.

        """
        return isinstance(other, OrderedSet) and list(self) == list(other)

    def __del__(self):
        """Destructor, clears self to avoid circular reference which could
        otherwise occur due to the doubly linked list.

        """
        self.clear()