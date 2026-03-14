import React, { useEffect, useRef, useState, useCallback } from 'react';
import cytoscape, { type Core, type EdgeSingular, type ElementDefinition } from 'cytoscape';
import { useAppStore } from '@/store';
import { api } from '@/lib/api';
import { Network, RefreshCw, ZoomIn, ZoomOut, Loader2, AlertCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { cn } from '@/lib/utils';

const LIMIT = 150;

/** Colors for entity types — use concrete HSL (Cytoscape doesn't support CSS variables). */
const ENTITY_TYPE_COLORS: Record<string, string> = {
    Person: 'hsl(210, 70%, 45%)',
    Company: 'hsl(280, 60%, 45%)',
    City: 'hsl(30, 70%, 45%)',
    Place: 'hsl(140, 50%, 40%)',
    Concept: 'hsl(0, 60%, 45%)',
    Date: 'hsl(45, 80%, 45%)',
    Entity: 'hsl(220, 60%, 50%)',
};
const DEFAULT_ENTITY_COLOR = 'hsl(220, 60%, 50%)';
const USER_COLOR = 'hsl(173, 58%, 39%)';
const MEMORY_COLOR = 'hsl(0, 0%, 52%)';

interface SelectedNodeInfo {
    id: string;
    label: string;
    type: string;
    nodeKind?: string;
    degree?: number;
    connections?: { relType: string; targetLabel: string; direction: 'in' | 'out' }[];
}

function SelectedNodePanel({ node }: { node: SelectedNodeInfo }) {
    const k = node.nodeKind || 'entity';
    return (
        <div className="p-3 border-t border-border/50 bg-muted/20 text-xs shrink-0 overflow-y-auto max-h-32">
            <div className="font-semibold text-foreground">{node.label}</div>
            <div className="text-muted-foreground mt-0.5">
                {k === 'entity' && (
                    <>
                        <span>Type: {node.type}</span>
                        {node.id && <span className="ml-2 opacity-70">ID: {String(node.id).slice(0, 12)}…</span>}
                    </>
                )}
                {k === 'user' && <span>Your profile — connections to known entities</span>}
                {k === 'memory' && (
                    <>
                        <span>Memory</span>
                        {node.id && <span className="ml-2 opacity-70">ID: {String(node.id).slice(0, 16)}…</span>}
                    </>
                )}
            </div>
            {node.degree !== undefined && node.degree > 0 && (
                <div className="mt-2 space-y-0.5">
                    <span className="text-muted-foreground">{node.degree} connection{node.degree !== 1 ? 's' : ''}</span>
                    {node.connections && node.connections.length > 0 && (
                        <ul className="mt-1 space-y-0.5 pl-3 list-disc">
                            {node.connections.map((c, i) => (
                                <li key={i} className="text-muted-foreground">
                                    <span className="text-foreground/80">{c.relType}</span>
                                    <span className="mx-1">{c.direction === 'out' ? '→' : '←'}</span>
                                    <span>{c.targetLabel}</span>
                                </li>
                            ))}
                            {node.degree > (node.connections?.length ?? 0) && (
                                <li className="text-muted-foreground/70">+{node.degree - (node.connections?.length ?? 0)} more</li>
                            )}
                        </ul>
                    )}
                </div>
            )}
        </div>
    );
}

export const KnowledgeGraphExplorer: React.FC = () => {
    const containerRef = useRef<HTMLDivElement>(null);
    const cyRef = useRef<Core | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [selectedNode, setSelectedNode] = useState<SelectedNodeInfo | null>(null);
    const spaces = useAppStore((s) => s.spaces);
    const currentSpaceId = useAppStore((s) => s.currentSpaceId);
    const setCurrentSpaceId = useAppStore((s) => s.setCurrentSpaceId);
    const fetchSpaces = useAppStore((s) => s.fetchSpaces);

    const fetchAndRender = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const { nodes, edges } = await api.getGraphExplore(
                currentSpaceId ?? undefined,
                LIMIT
            );
            if (!containerRef.current) return;

            // Destroy previous instance
            if (cyRef.current) {
                cyRef.current.destroy();
                cyRef.current = null;
            }

            const nodeKind = (n: { nodeKind?: string }) => (n as { nodeKind?: string }).nodeKind ?? 'entity';
            const entityType = (n: { type?: string }) => String((n as { type?: string }).type || 'Entity').trim();
            const getNodeColor = (n: { nodeKind?: string; type?: string }) => {
                const k = nodeKind(n);
                if (k === 'user') return USER_COLOR;
                if (k === 'memory') return MEMORY_COLOR;
                const t = entityType(n);
                return ENTITY_TYPE_COLORS[t] ?? ENTITY_TYPE_COLORS[t.charAt(0).toUpperCase() + t.slice(1).toLowerCase()] ?? DEFAULT_ENTITY_COLOR;
            };

            const elements: ElementDefinition[] = [
                ...nodes.map((n) => {
                    const k = nodeKind(n);
                    return {
                        data: {
                            id: n.id,
                            label: n.label,
                            type: n.type,
                            nodeKind: k,
                            entityType: entityType(n),
                            nodeColor: getNodeColor(n),
                        },
                    };
                }),
                ...edges.map((e, i) => {
                    const t = (e.type && String(e.type).trim()) || '';
                    return {
                        data: {
                            id: `e${i}`,
                            source: e.source,
                            target: e.target,
                            relType: t,
                            relLabel: t,
                        },
                    };
                }),
            ];

            const cy = cytoscape({
                container: containerRef.current,
                elements,
                style: [
                    {
                        selector: 'node',
                        style: {
                            label: 'data(label)',
                            'text-valign': 'bottom',
                            'text-halign': 'center',
                            'font-size': '10px',
                            'text-margin-y': 4,
                            width: 24,
                            height: 24,
                            'background-color': 'data(nodeColor)',
                            color: '#111827',
                            'text-wrap': 'ellipsis',
                            'text-max-width': '80px',
                            'border-width': 2,
                            'border-color': 'data(nodeColor)',
                            shape: 'ellipse',
                        },
                    },
                    {
                        selector: 'node[nodeKind="user"]',
                        style: {
                            shape: 'diamond',
                            width: 32,
                            height: 32,
                        },
                    },
                    {
                        selector: 'node[nodeKind="memory"]',
                        style: {
                            shape: 'rectangle',
                            width: 20,
                            height: 20,
                            'font-size': '8px',
                        },
                    },
                    {
                        selector: 'node:selected',
                        style: {
                            'border-width': 3,
                            'border-color': '#3b82f6',
                            'background-color': '#60a5fa',
                        },
                    },
                    {
                        selector: 'edge',
                        style: {
                            width: 1.5,
                            'line-color': '#94a3b8',
                            'target-arrow-color': '#94a3b8',
                            'target-arrow-shape': 'triangle',
                            'curve-style': 'bezier',
                            label: 'data(relLabel)',
                            'font-size': '9px',
                            color: '#94a3b8',
                            'text-rotation': 'autorotate',
                        },
                    },
                    {
                        selector: 'edge[relType="CONTAINS_ENTITY"]',
                        style: {
                            'line-color': '#64748b',
                            'target-arrow-color': '#64748b',
                            width: 1,
                            'target-arrow-shape': 'none',
                            label: '',
                        },
                    },
                ],
                layout: {
                    name: 'cose',
                    idealEdgeLength: 80,
                    nodeOverlap: 20,
                    padding: 40,
                },
                minZoom: 0.2,
                maxZoom: 3,
                wheelSensitivity: 0.3,
            });

            cy.on('tap', 'node', (evt) => {
                const n = evt.target;
                const data = n.data();
                const connectedEdges = n.connectedEdges();
                const connections: { relType: string; targetLabel: string; direction: 'in' | 'out' }[] = [];
                connectedEdges.forEach((edge: EdgeSingular) => {
                    const relType = edge.data('relType') || 'RELATED_TO';
                    const source = edge.source();
                    const target = edge.target();
                    const other = source.id() === data.id ? target : source;
                    const direction = target.id() === data.id ? 'in' : 'out';
                    connections.push({
                        relType,
                        targetLabel: other.data('label') || other.id(),
                        direction,
                    });
                });
                setSelectedNode({
                    id: data.id,
                    label: data.label,
                    type: data.type,
                    nodeKind: data.nodeKind,
                    degree: connectedEdges.length,
                    connections: connections.slice(0, 12),
                });
            });
            cy.on('tap', (evt) => {
                if (evt.target === cy) setSelectedNode(null);
            });

            cyRef.current = cy;
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : 'Failed to load graph';
            setError(msg);
        } finally {
            setLoading(false);
        }
    }, [currentSpaceId]);

    useEffect(() => {
        fetchSpaces();
    }, [fetchSpaces]);

    useEffect(() => {
        fetchAndRender();
        return () => {
            if (cyRef.current) {
                cyRef.current.destroy();
                cyRef.current = null;
            }
        };
    }, [fetchAndRender]);

    const handleZoomIn = () => cyRef.current?.zoom(cyRef.current.zoom() * 1.2);
    const handleZoomOut = () => cyRef.current?.zoom(cyRef.current.zoom() / 1.2);
    const handleFit = () => cyRef.current?.fit(undefined, 40);

    return (
        <div className="h-full flex flex-col bg-background">
            {/* Toolbar */}
            <div className="flex items-center justify-between gap-2 p-2 border-b border-border/50 bg-muted/20 shrink-0">
                <div className="flex items-center gap-2">
                    <Select value={currentSpaceId ?? '__global__'} onValueChange={(v) => setCurrentSpaceId(v === '__global__' ? null : v)}>
                        <SelectTrigger className="w-[180px] h-8 text-xs bg-background">
                            <SelectValue placeholder="Space" />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="__global__">Global</SelectItem>
                            {spaces.map((s) => (
                                <SelectItem key={s.space_id} value={s.space_id}>
                                    {s.name || 'Unnamed'}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <Button variant="outline" size="sm" className="h-8 gap-1.5" onClick={fetchAndRender} disabled={loading}>
                        {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                        Refresh
                    </Button>
                </div>
                <div className="flex items-center gap-1">
                    <Button variant="ghost" size="icon" className="h-8 w-8" onClick={handleZoomOut} title="Zoom out">
                        <ZoomOut className="w-4 h-4" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-8 w-8" onClick={handleFit} title="Fit">
                        <Network className="w-4 h-4" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-8 w-8" onClick={handleZoomIn} title="Zoom in">
                        <ZoomIn className="w-4 h-4" />
                    </Button>
                </div>
            </div>

            {/* Graph canvas / content */}
            <div className="flex-1 min-h-0 relative">
                {loading && (
                    <div className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-background/80">
                        <Loader2 className="w-12 h-12 animate-spin text-primary" />
                        <p className="mt-2 text-sm text-muted-foreground">Loading knowledge graph...</p>
                    </div>
                )}
                {error && (
                    <div className="absolute inset-0 z-10 flex flex-col items-center justify-center p-8 text-center">
                        <AlertCircle className="w-12 h-12 text-destructive mb-2" />
                        <p className="text-sm text-destructive mb-4">{error}</p>
                        <Button variant="outline" size="sm" onClick={fetchAndRender}>
                            <RefreshCw className="w-4 h-4 mr-2" /> Retry
                        </Button>
                    </div>
                )}
                <div
                    ref={containerRef}
                    className={cn(
                        "w-full h-full min-h-[300px] rounded-lg",
                        (loading || error) && "opacity-30 pointer-events-none"
                    )}
                />
            </div>

            {/* Selected node detail */}
            {selectedNode && (
                <SelectedNodePanel node={selectedNode} />
            )}
        </div>
    );
};
